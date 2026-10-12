import pytest
from procedure_helpers import (
    FailingEmbedder, FakeEmbedder, days_ago, make_engine, recipe, seed_version, settings,
)
from water_source_research.core.capabilities import Capability
from water_source_research.core.versions import WORKFLOW_VERSION
from water_source_research.procedures.ranking import RankInput, RankingPolicy, wilson_lower_bound
from water_source_research.procedures.repository import ProcedureRepository
from water_source_research.procedures.retrieval import ProcedureMemory, choose_strategy
from water_source_research.schemas.procedures import ProcedureStatus, RetrievalRequest
from water_source_research.context_manager import (
    ContextManager, ContextPurpose, ContextStatus, ContextTask, ProcedureHint,
)
from water_source_research.services.field_state import initialize_city_state

@pytest.fixture
def repo(tmp_path):
    engine = make_engine(tmp_path)
    yield ProcedureRepository(engine)
    engine.dispose()

@pytest.fixture
def embedder():
    return FakeEmbedder()

def request(field_type="withdrawal", **kw):
    return RetrievalRequest(task_type=f"{field_type}_research", field_type=field_type,
                            workflow_version=WORKFLOW_VERSION, **kw)

def memory(repo, embedder, **overrides):
    return ProcedureMemory(repo, embedder, settings(**overrides))

# --- metadata filtering ---------------------------------------------------------------

def test_metadata_filter_runs_before_similarity(repo, embedder):
    wanted = seed_version(repo, recipe("withdrawal"), embedder=embedder)
    seed_version(repo, recipe("water_quality", sources=("regulatory_report",)),
                 embedder=embedder)
    seed_version(repo, recipe("withdrawal", sources=("government",),
                              queries=("{city} water withdrawal statistics",)),
                 status=ProcedureStatus.DEPRECATED, embedder=embedder)
    seen_ids = []
    original = repo.similarities
    repo.similarities = lambda ids, *rest: seen_ids.extend(ids) or original(ids, *rest)
    result = memory(repo, embedder).retrieve(request())
    assert seen_ids == [wanted]  # similarity computed only for metadata survivors
    assert [m.procedure_version_id for m in result.matches] == [wanted]

def test_wrong_field_type_excluded(repo, embedder):
    seed_version(repo, recipe("water_quality", sources=("regulatory_report",)),
                 embedder=embedder)
    assert memory(repo, embedder).retrieve(request("withdrawal")).matches == []

def test_deprecated_and_candidate_excluded_by_default(repo, embedder):
    seed_version(repo, recipe(), status=ProcedureStatus.DEPRECATED, embedder=embedder)
    seed_version(repo, recipe(sources=("government",)), status=ProcedureStatus.CANDIDATE,
                 embedder=embedder)
    assert memory(repo, embedder).retrieve(request()).matches == []
    # Explicit configuration can widen the status filter (e.g. for evaluation).
    wide = memory(repo, embedder, statuses=[ProcedureStatus.CANDIDATE])
    assert len(wide.retrieve(request()).matches) == 1

def test_incompatible_workflow_excluded(repo, embedder):
    vid = seed_version(repo, recipe(), embedder=embedder, min_workflow_version=WORKFLOW_VERSION + 1)
    assert memory(repo, embedder).retrieve(request()).matches == []
    # A workflow version that doesn't know the required capabilities excludes it too.
    seed_version(repo, recipe(sources=("government",)), embedder=embedder)
    old = RetrievalRequest(task_type="withdrawal_research", field_type="withdrawal",
                           workflow_version=1)
    assert memory(repo, embedder).retrieve(old).matches == []
    assert vid

def test_water_type_compatibility(repo, embedder):
    ground = seed_version(repo, recipe(water_type="Groundwater",
                                       sources=("hydrogeological_survey",)), embedder=embedder)
    generic = seed_version(repo, recipe(), embedder=embedder)
    surface = memory(repo, embedder).retrieve(request(water_type="Surface"))
    assert {m.procedure_version_id for m in surface.matches} == {generic}
    gw = memory(repo, embedder).retrieve(request(water_type="Groundwater"))
    assert {m.procedure_version_id for m in gw.matches} == {ground, generic}

# --- ranking ------------------------------------------------------------------------

def test_top_n_limit_respected(repo, embedder):
    for i in range(6):
        seed_version(repo, recipe(sources=(f"source_{i}",)), embedder=embedder)
    assert len(memory(repo, embedder, max_results=2).retrieve(request()).matches) == 2
    assert len(memory(repo, embedder).retrieve(request()).matches) == 3

def test_ranking_is_stable(repo, embedder):
    for i in range(4):
        seed_version(repo, recipe(sources=(f"source_{i}",)), embedder=embedder)
    mem = memory(repo, embedder)
    first = [m.procedure_version_id for m in mem.retrieve(request()).matches]
    for _ in range(3):
        assert [m.procedure_version_id for m in mem.retrieve(request()).matches] == first

def test_small_sample_does_not_beat_large_proven_sample(repo, embedder):
    proven = seed_version(repo, recipe(sources=("a",)), embedder=embedder,
                          success_count=680, failure_count=120, usage_count=800,
                          recent_success_rate=0.85, last_success_at=days_ago(1))
    lucky = seed_version(repo, recipe(sources=("b",)), embedder=embedder,
                         success_count=2, failure_count=0, usage_count=2,
                         recent_success_rate=1.0, last_success_at=days_ago(1))
    matches = memory(repo, embedder).retrieve(request()).matches
    assert [m.procedure_version_id for m in matches][:2] == [proven, lucky]
    assert wilson_lower_bound(2, 2, 1.96) < wilson_lower_bound(680, 800, 1.96)

def test_human_corrections_lower_rank():
    policy = RankingPolicy(settings())
    now = days_ago(0)
    clean = RankInput("p1", "v1", 1, 50, 10, 60, 0.8, 2, now, now)
    corrected = RankInput("p2", "v2", 1, 50, 10, 60, 0.8, 42, now, now)
    assert policy.score(clean, 0.8, now).score > policy.score(corrected, 0.8, now).score
    assert any("human-correction" in r for r in policy.score(corrected, 0.8, now).reasons)

def test_min_similarity_threshold(repo, embedder):
    seed_version(repo, recipe(), embedder=embedder)
    assert memory(repo, embedder, min_similarity=0.99).retrieve(request()).matches == []

# --- no match / degradation ---------------------------------------------------------

def test_no_match_returns_empty_not_degraded(repo, embedder):
    result = memory(repo, embedder).retrieve(request())
    assert result.matches == [] and not result.degraded

def test_vector_failure_degrades_to_reliability_ranking(repo):
    vid = seed_version(repo, recipe(), embedder=FakeEmbedder())
    result = memory(repo, FailingEmbedder()).retrieve(request())
    assert result.vector_degraded and not result.degraded
    assert [m.procedure_version_id for m in result.matches] == [vid]
    assert result.matches[0].similarity_score is None

def test_repository_outage_returns_degraded_empty(repo, embedder, spans):
    def boom(*a, **k):
        raise ConnectionError("database unavailable")

    repo.filter_candidates = boom
    result = memory(repo, embedder).retrieve(request())
    assert result.degraded and result.matches == []
    root = next(s for s in spans.get_finished_spans() if s.name == "procedure_memory.retrieve")
    assert root.status.status_code.name == "ERROR"
    assert root.attributes["procedure.retrieval_outcome"] == "degraded"

# --- usage bookkeeping ----------------------------------------------------------------

def test_retrieval_records_usage_without_counting_success(repo, embedder):
    vid = seed_version(repo, recipe(), embedder=embedder)
    memory(repo, embedder).retrieve(
        request(run_id="run-1", field_keys=["total_city_water_withdrawals_m3_day"]))
    [usage] = repo.usages_for_run("run-1")
    assert (usage["selected"], usage["applied"], usage["result"]) == (False, False, "PENDING")
    v = repo.get_version(vid)
    assert v.success_count == 0 and v.usage_count == 0
    # Re-retrieval in the same run doesn't duplicate rows.
    memory(repo, embedder).retrieve(
        request(run_id="run-1", field_keys=["total_city_water_withdrawals_m3_day"]))
    assert len(repo.usages_for_run("run-1")) == 1

# --- planner choice -------------------------------------------------------------------

def test_planner_drops_unavailable_capabilities(repo, embedder):
    seed_version(repo, recipe(caps=(Capability.SOURCE_DISCOVERY,
                                    Capability.DYNAMIC_DOCUMENT_ACQUISITION)),
                 embedder=embedder)
    matches = memory(repo, embedder).retrieve(request()).matches
    selection = choose_strategy(matches, frozenset({Capability.SOURCE_DISCOVERY}))
    assert selection.adapted
    assert selection.dropped_capabilities == ["dynamic_document_acquisition"]
    assert choose_strategy(matches, frozenset()) is None  # nothing usable -> default strategy

# --- telemetry ------------------------------------------------------------------------

def test_retrieval_emits_phase_spans_and_metrics(repo, embedder, spans, metric_points):
    seed_version(repo, recipe(), embedder=embedder)
    memory(repo, embedder).retrieve(request(run_id="run-9"))
    names = [s.name for s in spans.get_finished_spans()]
    for phase in ("procedure_memory.filter", "procedure_memory.vector_search",
                  "procedure_memory.rank", "procedure_memory.select",
                  "procedure_memory.retrieve"):
        assert phase in names
    by_name = {s.name: s for s in spans.get_finished_spans()}
    assert by_name["procedure_memory.filter"].attributes["procedure.candidate_count"] == 1
    assert by_name["procedure_memory.select"].attributes["procedure.selected_count"] == 1
    assert "procedure.top_similarity" in by_name["procedure_memory.vector_search"].attributes
    assert by_name["procedure_memory.retrieve"].attributes["procedure.retrieval_latency_ms"] >= 0
    points = metric_points()
    assert any(a.get("outcome") == "matched" and a.get("field_type") == "withdrawal"
               for a, _ in points["procedure.retrieval.requests"])
    # Low-cardinality dimensions only: no run or procedure ids on metrics.
    for a, _ in points["procedure.retrieval.requests"]:
        assert set(a) <= {"field_type", "task_type", "outcome"}

# --- fact vs procedure boundary (Context Manager) -------------------------------------

HINT = ProcedureHint("proc-1", 3, "Prioritise utility annual reports.\n1. source_discovery")

def test_procedure_hints_are_separate_from_evidence():
    state = initialize_city_state("Nairobi", "Kenya")
    bundle = ContextManager().build(
        ContextTask(ContextPurpose.PLAN, ["total_city_water_withdrawals_m3_day"],
                    procedure_hints=(HINT,)), state)
    rendered = bundle.render()
    assert "## Procedure guidance (advisory" in rendered
    assert "[proc-1 v3] Prioritise utility annual reports." in rendered
    assert bundle.sources == {}  # no S# citation ids were minted for procedure text
    assert "[S1]" not in rendered
    assert bundle.consumed == set()
    with pytest.raises(KeyError):
        bundle.evidence_ref("S1")

def test_procedure_hints_cannot_satisfy_evidence_requirement():
    state = initialize_city_state("Nairobi", "Kenya")
    bundle = ContextManager().build(
        ContextTask(ContextPurpose.EXTRACT, ["total_city_water_withdrawals_m3_day"],
                    procedure_hints=(HINT,)), state, chunks=[])
    assert bundle.status is ContextStatus.NO_SUFFICIENT_EVIDENCE
    assert "Procedure guidance" not in bundle.render()
    assert bundle.stats["procedure_hints_ignored"] == 1

def test_hints_affect_plan_cache_key_only():
    mgr, state = ContextManager(), initialize_city_state("Nairobi", "Kenya")
    fields = ["total_city_water_withdrawals_m3_day"]
    v3, v4 = (HINT,), (ProcedureHint("proc-1", 4, HINT.text),)
    plan = lambda h: mgr.cache_key(ContextTask(ContextPurpose.PLAN, fields, procedure_hints=h),
                                   state)
    extract = lambda h: mgr.cache_key(
        ContextTask(ContextPurpose.EXTRACT, fields, procedure_hints=h), state)
    assert plan(v3) != plan(v4)
    assert extract(v3) == extract(v4) == extract(())

def test_hint_budget_is_bounded():
    state = initialize_city_state("Nairobi", "Kenya")
    hints = tuple(ProcedureHint(f"p{i}", 1, "x" * 400) for i in range(5))
    bundle = ContextManager(procedure_hint_budget=250).build(
        ContextTask(ContextPurpose.PLAN, ["total_city_water_withdrawals_m3_day"],
                    procedure_hints=hints), state)
    assert bundle.stats["procedure_hints"] == 2
    assert bundle.stats["procedure_hints_dropped_budget"] == 3
