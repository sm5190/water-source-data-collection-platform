import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from procedure_helpers import PRIMARY, FakeEmbedder, make_engine, recipe, run_state, seed_version, settings
from water_source_research.core.versions import WORKFLOW_VERSION
from water_source_research.procedures.ranking import RankingPolicy
from water_source_research.procedures.repository import ProcedureRepository, UsageOutcome
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.usage import UsageTracker, field_outcome
from water_source_research.schemas.procedures import RetrievalRequest

@pytest.fixture
def repo(tmp_path):
    engine = make_engine(tmp_path)
    yield ProcedureRepository(engine)
    engine.dispose()

def retrieve(repo, run_id, fields):
    return ProcedureMemory(repo, FakeEmbedder(), settings()).retrieve(RetrievalRequest(
        task_type="withdrawal_research", field_type="withdrawal",
        workflow_version=WORKFLOW_VERSION, run_id=run_id, field_keys=fields))

def outcome(success=True, corrected=False):
    return UsageOutcome(success=success, field_resolved=success, validation_result="ACCEPTED",
                        tool_calls=3, search_rounds=2, cost=0.01, latency_ms=1200.0,
                        human_review_required=corrected, human_corrected=corrected)

def test_retrieved_selected_applied_are_distinct(repo):
    vid = seed_version(repo, recipe())
    retrieve(repo, "run-1", [PRIMARY, "total_city_water_withdrawals_year"])
    v = repo.get_version(vid)
    assert (v.usage_count, v.success_count) == (0, 0)
    tracker = UsageTracker(repo, settings())
    assert tracker.record_selection(vid, "run-1", [PRIMARY], adapted=True,
                                    field_type="withdrawal") == 1
    usages = {u["field_key"]: u for u in repo.usages_for_run("run-1")}
    assert usages[PRIMARY]["selected"] and usages[PRIMARY]["adapted"]
    assert not usages["total_city_water_withdrawals_year"]["selected"]
    assert not usages[PRIMARY]["applied"]
    # An outcome for a selected-but-not-applied usage does not count.
    assert not repo.record_outcome(vid, "run-1", PRIMARY, outcome(), 0.2)
    assert tracker.record_application(vid, "run-1", [PRIMARY]) == 1
    assert repo.get_version(vid).usage_count == 1
    assert repo.record_outcome(vid, "run-1", PRIMARY, outcome(), 0.2)
    v = repo.get_version(vid)
    assert (v.success_count, v.failure_count, v.recent_success_rate) == (1, 0, 1.0)

def test_outcome_recorded_exactly_once(repo):
    vid = seed_version(repo, recipe())
    retrieve(repo, "run-1", [PRIMARY])
    repo.mark_selected(vid, "run-1", [PRIMARY], False)
    repo.mark_applied(vid, "run-1", [PRIMARY])
    assert repo.record_outcome(vid, "run-1", PRIMARY, outcome(), 0.2)
    assert not repo.record_outcome(vid, "run-1", PRIMARY, outcome(success=False), 0.2)
    v = repo.get_version(vid)
    assert (v.success_count, v.failure_count) == (1, 0)

def test_concurrent_outcomes_do_not_lose_updates(repo):
    vid = seed_version(repo, recipe())
    runs = [f"run-{i}" for i in range(12)]
    for run in runs:
        retrieve(repo, run, [PRIMARY])
        repo.mark_selected(vid, run, [PRIMARY], False)
        repo.mark_applied(vid, run, [PRIMARY])
    barrier = threading.Barrier(len(runs))

    def record(i):
        barrier.wait()
        return ProcedureRepository(repo.engine).record_outcome(
            vid, runs[i], PRIMARY, outcome(success=i % 3 != 0), 0.2)

    with ThreadPoolExecutor(len(runs)) as pool:
        assert all(pool.map(record, range(len(runs))))
    v = repo.get_version(vid)
    assert (v.success_count, v.failure_count, v.usage_count) == (8, 4, 12)
    assert 0 < v.recent_success_rate < 1

def test_human_corrections_reduce_quality(repo):
    clean = seed_version(repo, recipe(sources=("a",)))
    corrected = seed_version(repo, recipe(sources=("b",)))
    for i in range(10):
        run = f"run-{i}"
        retrieve(repo, run, [PRIMARY])
        for vid, fix in ((clean, False), (corrected, i < 7)):
            repo.mark_selected(vid, run, [PRIMARY], False)
            repo.mark_applied(vid, run, [PRIMARY])
            repo.record_outcome(vid, run, PRIMARY, outcome(corrected=fix), 0.2)
    a, b = repo.get_version(clean), repo.get_version(corrected)
    assert (a.success_count, b.success_count) == (10, 10)  # both "succeeded"...
    assert b.human_correction_count == 7  # ...but one needed correction 70% of the time
    policy = RankingPolicy(settings())
    from water_source_research.procedures.ranking import RankInput
    score = lambda v: policy.score(RankInput(v.procedure_id, v.id, v.version, v.success_count,
                                             v.failure_count, v.usage_count,
                                             v.recent_success_rate, v.human_correction_count,
                                             v.last_success_at, v.created_at),
                                   0.8, v.created_at)
    assert score(a).score > score(b).score

def test_field_outcome_derived_from_trusted_events():
    state = run_state("r1", "Toronto", corrected=[PRIMARY])
    o = field_outcome(state, PRIMARY)
    assert o.field_resolved and o.success
    assert o.human_corrected and o.human_review_required
    assert (o.tool_calls, o.search_rounds) == (3, 2)
    unresolved = field_outcome(state, "water_quality_notes")
    assert not unresolved.success and unresolved.tool_calls == 0
