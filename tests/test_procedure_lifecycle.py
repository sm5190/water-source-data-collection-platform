import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from procedure_helpers import (
    FakeEmbedder, days_ago, make_engine, make_worker, persist_run, recipe, run_state,
    seed_version, settings,
)
from water_source_research.core.versions import WORKFLOW_VERSION
from water_source_research.procedures.lifecycle import LifecycleManager, LifecyclePolicy
from water_source_research.procedures.repository import ProcedureRepository
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.worker import JobOutcome
from water_source_research.schemas.procedures import ProcedureStatus as S, RetrievalRequest

@pytest.fixture
def repo(tmp_path):
    engine = make_engine(tmp_path)
    yield ProcedureRepository(engine)
    engine.dispose()

def manager(repo, **kw):
    return LifecycleManager(repo, LifecyclePolicy(settings(**kw)))

def distill_cities(repo, cities, corrected=False, **kw):
    out = []
    for i, city in enumerate(cities):
        state = run_state(f"run-{city}-{i}", city,
                          corrected=["total_city_water_withdrawals_m3_day"] if corrected else ())
        persist_run(repo.engine, state)
        out += [r for r in make_worker(repo, **kw).run_once(limit=20)
                if r.outcome in (JobOutcome.CREATED, JobOutcome.MERGED)]
    return out

def withdrawal_request():
    return RetrievalRequest(task_type="withdrawal_research", field_type="withdrawal",
                            workflow_version=WORKFLOW_VERSION)

# --- promotion ------------------------------------------------------------------------

def test_one_run_stays_candidate(repo):
    [r] = distill_cities(repo, ["Toronto"])
    assert repo.get_version(r.procedure_version_id).status == S.CANDIDATE

def test_policy_promotion_without_approval(repo):
    results = distill_cities(repo, ["Toronto", "Madrid", "Lisbon"],
                             require_human_approval=False)
    v = repo.get_version(results[-1].procedure_version_id)
    assert v.status == S.ACTIVE and (v.support_runs, v.support_cities) == (3, 3)

def test_policy_promotion_waits_for_human_approval(repo):
    results = distill_cities(repo, ["Toronto", "Madrid", "Lisbon"])
    vid = results[-1].procedure_version_id
    assert repo.get_version(vid).status == S.AWAITING_APPROVAL
    mem = ProcedureMemory(repo, FakeEmbedder(), settings())
    assert mem.retrieve(withdrawal_request()).matches == []  # not retrievable until approved
    assert manager(repo).approve(vid, reviewer="reviewer@example.org")
    v = repo.get_version(vid)
    assert v.status == S.ACTIVE and v.approved_at is not None
    assert len(mem.retrieve(withdrawal_request()).matches) == 1

def test_same_city_repeats_do_not_satisfy_city_diversity(repo):
    results = distill_cities(repo, ["Toronto", "Toronto", "Toronto"],
                             require_human_approval=False)
    v = repo.get_version(results[-1].procedure_version_id)
    assert (v.support_runs, v.support_cities, v.status) == (3, 1, S.CANDIDATE)

def test_heavily_corrected_support_blocks_promotion(repo):
    results = distill_cities(repo, ["Toronto", "Madrid", "Lisbon"], corrected=True,
                             require_human_approval=False)
    v = repo.get_version(results[-1].procedure_version_id)
    assert v.support_corrected_runs == 3 and v.status == S.CANDIDATE

# --- one active version / history -----------------------------------------------------

def test_promoting_new_version_deprecates_old_and_keeps_history(repo):
    v1 = seed_version(repo, recipe(), procedure_tag="same")
    v1_recipe = repo.get_version(v1).recipe_json
    v2 = seed_version(repo, recipe(sources=("regulatory_report",)), procedure_tag="same",
                      status=S.CANDIDATE)
    assert repo.promote(v2, {S.CANDIDATE})
    versions = repo.versions_for_procedure(repo.get_version(v1).procedure_id)
    assert [(v.version, v.status) for v in versions] == [(1, S.DEPRECATED), (2, S.ACTIVE)]
    assert repo.get_version(v1).recipe_json == v1_recipe  # never overwritten

def test_concurrent_promotions_leave_exactly_one_active(repo):
    ids = [seed_version(repo, recipe(sources=(f"s{i}",)), procedure_tag="same",
                        status=S.CANDIDATE) for i in range(4)]
    barrier = threading.Barrier(len(ids))

    def promote(vid):
        barrier.wait()
        return ProcedureRepository(repo.engine).promote(vid, {S.CANDIDATE})

    with ThreadPoolExecutor(len(ids)) as pool:
        list(pool.map(promote, ids))
    statuses = [v.status for v in repo.versions_for_procedure(repo.get_version(ids[0]).procedure_id)]
    assert statuses.count(S.ACTIVE) == 1

def test_transition_is_optimistic(repo):
    vid = seed_version(repo, recipe())
    assert repo.transition(vid, {S.ACTIVE}, S.DEPRECATED)
    assert not repo.transition(vid, {S.ACTIVE}, S.DEPRECATED)  # stale expectation
    with pytest.raises(ValueError):
        repo.transition(vid, {S.DEPRECATED}, S.ACTIVE)

# --- deprecation / retirement ---------------------------------------------------------

def test_recently_failing_active_procedure_is_deprecated(repo):
    vid = seed_version(repo, recipe(), success_count=4, failure_count=16,
                       recent_success_rate=0.2, last_success_at=days_ago(1))
    [t] = manager(repo).sweep()
    assert (t.version_id, t.to_status) == (vid, S.DEPRECATED)
    assert "recent success" in t.reason

def test_stale_active_procedure_is_deprecated(repo):
    seed_version(repo, recipe(), last_success_at=days_ago(400), status_changed_at=days_ago(400))
    [t] = manager(repo).sweep()
    assert t.to_status == S.DEPRECATED and "stale" in t.reason

def test_workflow_incompatible_procedure_is_deprecated(repo):
    seed_version(repo, recipe(), min_workflow_version=WORKFLOW_VERSION + 1)
    [t] = manager(repo).sweep()
    assert "workflow" in t.reason

def test_healthy_active_procedure_untouched(repo):
    seed_version(repo, recipe(), success_count=40, failure_count=5, recent_success_rate=0.9,
                 last_success_at=days_ago(2))
    assert manager(repo).sweep() == []

def test_deprecated_retires_after_window_and_is_never_retrieved(repo):
    vid = seed_version(repo, recipe(), status=S.DEPRECATED, status_changed_at=days_ago(200))
    mem = ProcedureMemory(repo, FakeEmbedder(), settings())
    assert mem.retrieve(withdrawal_request()).matches == []
    [t] = manager(repo).sweep()
    assert (t.version_id, t.to_status) == (vid, S.RETIRED)

def test_manual_deprecate_and_reject(repo):
    active = seed_version(repo, recipe())
    waiting = seed_version(repo, recipe(sources=("x",)), status=S.AWAITING_APPROVAL)
    m = manager(repo)
    assert m.deprecate(active) and repo.get_version(active).status == S.DEPRECATED
    assert m.reject(waiting) and repo.get_version(waiting).status == S.REJECTED
    assert not m.approve(waiting, "late reviewer")
