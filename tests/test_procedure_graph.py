import asyncio
import pytest
from fastapi.testclient import TestClient
from procedure_helpers import (
    PRIMARY, FakeEmbedder, app_settings, make_engine, recipe, seed_version, settings,
)
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.core.capabilities import Capability
from water_source_research.procedures.repository import ProcedureRepository
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.services import build_procedure_services
from water_source_research.procedures.worker import JobOutcome
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.procedures import ProcedureStatus
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.run_recorder import RunRecorder

ALL_CAPS = frozenset(Capability)
CHUNK = EvidenceChunk(
    chunk_id="w", url="https://toronto.ca/water/annual-report.pdf", source_type="UTILITY",
    text="Toronto average daily production (withdrawal) was 1,050 million litres per day "
         "(MLD) in 2023, according to the annual report.")

class FakeGateway:
    def __init__(self):
        self.calls = []

    async def extract(self, bundle, *, run_id, field_key):
        self.calls.append(field_key)
        # Cite the evidence passage actually used (known facts may also carry S# ids).
        cited = next(i.source_id for i in bundle.items if i.kind == "evidence")
        return [FieldClaim(field_key=field_key, value=1_050_000, confidence=0.9,
                           evidence=[bundle.evidence_ref(cited)])]

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path)
    yield eng
    eng.dispose()

@pytest.fixture
def repo(engine):
    return ProcedureRepository(engine)

def seeded(repo, **kw):
    return seed_version(repo, recipe(queries=("{utility} annual water report",
                                              "{city} average daily production mgd"), **kw),
                        embedder=FakeEmbedder())

def initial(fields=(PRIMARY,), chunks=(), decisions=(), run_id="run-1"):
    research = initialize_city_state("Toronto", "Canada")
    fs = research.fields["utility_name"]
    fs.status = FieldResolutionStatus.RESOLVED
    fs.selected_claim = FieldClaim(field_key="utility_name", value="Toronto Water",
                                   confidence=0.95,
                                   evidence=[EvidenceRef(url="https://toronto.ca/water",
                                                         passage="Toronto Water")])
    return {"run_id": run_id, "city_name": "Toronto", "country": "Canada",
            "research_state": research.model_dump(mode="json"),
            "unresolved_fields": list(fields),
            "evidence_chunks": [c.model_dump(mode="json") for c in chunks],
            "review_decisions": list(decisions)}

def run(graph, state):
    return asyncio.run(graph.ainvoke(state))

def graph_with(repo, gateway=None, recorder=None, memory=None, available=ALL_CAPS, rounds=1):
    return build_graph(gateway=gateway or FakeGateway(), max_discovery_rounds=rounds,
                       procedure_memory=memory or ProcedureMemory(repo, FakeEmbedder(),
                                                                  settings()),
                       run_recorder=recorder, capability_availability=available)

# --- guidance used, usage tracked ---------------------------------------------------

def test_selected_procedure_guides_planning_and_outcome_is_tracked(engine, repo):
    vid = seeded(repo)
    result = run(graph_with(repo, recorder=RunRecorder(engine)),
                 initial(chunks=[CHUNK], decisions=[{"field_key": PRIMARY,
                                                     "decision": "APPROVED"}]))
    [sel] = result["procedure_selections"]
    assert sel["procedure_version_id"] == vid and sel["applied"]
    assert result["planned_queries"]["withdrawal"] == [
        "Toronto Water annual water report", "Toronto average daily production mgd"]
    field = result["research_state"]["fields"][PRIMARY]
    assert field["status"] == "RESOLVED"
    # The accepted value came from evidence, cited to the evidence URL, not from memory.
    assert field["selected_claim"]["evidence"][0]["url"] == CHUNK.url
    [usage] = repo.usages_for_run("run-1")
    assert (usage["selected"], usage["applied"], usage["result"]) == (True, True, "SUCCESS")
    v = repo.get_version(vid)
    assert (v.usage_count, v.success_count) == (1, 1)
    assert result["distillation_jobs"]  # trusted events persisted, distillation enqueued
    assert result["post_processing_errors"] == []

def test_procedure_memory_never_creates_facts(repo):
    vid = seeded(repo)
    gateway = FakeGateway()
    result = run(graph_with(repo, gateway=gateway), initial())  # no evidence at all
    assert gateway.calls == []
    assert not result.get("claims")
    assert result["research_state"]["fields"][PRIMARY]["status"] == "UNRESOLVED"
    assert result["fields_needing_evidence"] == [PRIMARY]
    v = repo.get_version(vid)
    assert (v.success_count, v.failure_count) == (0, 1)  # applied, but it did not help

def test_accepted_field_types_are_not_retrieved(repo):
    seeded(repo)
    memory = ProcedureMemory(repo, FakeEmbedder(), settings())
    asked = []
    original = memory.aretrieve

    async def spy(req):
        asked.append(req.field_type)
        return await original(req)

    memory.aretrieve = spy
    run(graph_with(repo, memory=memory), initial(fields=("utility_name", PRIMARY)))
    assert asked == ["withdrawal"]  # utility already accepted: no utility_research lookup

# --- degraded modes -------------------------------------------------------------------

def test_procedure_memory_outage_uses_default_strategy(repo):
    seeded(repo)

    def down(*a, **k):
        raise ConnectionError("pgvector unavailable")

    repo.filter_candidates = down
    result = run(graph_with(repo), initial(chunks=[CHUNK]))
    assert result["finalized"] and result["procedure_memory_degraded"]
    assert result["procedure_selections"] == []
    plan_events = [e for e in result["events"] if e["node"] == "plan"]
    assert plan_events[0]["strategy"] == "default:degraded"
    assert result["research_state"]["fields"][PRIMARY]["status"] == "CANDIDATE"

def test_total_procedure_memory_failure_does_not_break_run(repo):
    class Broken(ProcedureMemory):
        async def aretrieve(self, req):
            raise RuntimeError("process crashed")

    result = run(graph_with(repo, memory=Broken(repo, None, settings())),
                 initial(chunks=[CHUNK]))
    assert result["finalized"] and result["procedure_memory_degraded"]

def test_unavailable_capability_means_planner_ignores_procedure(repo):
    seed_version(repo, recipe(caps=(Capability.DYNAMIC_DOCUMENT_ACQUISITION,)),
                 embedder=FakeEmbedder())
    result = run(graph_with(repo, available=frozenset({Capability.SOURCE_DISCOVERY})),
                 initial(chunks=[CHUNK]))
    assert result["procedure_guidance"]["withdrawal"]  # retrieved...
    assert result["procedure_selections"] == []  # ...but not usable now: default strategy

def test_post_processing_failure_does_not_change_scientific_result(engine, repo):
    baseline = run(graph_with(repo), initial(chunks=[CHUNK]))

    class FailingRecorder(RunRecorder):
        def persist(self, state):
            raise OSError("database disk full")

    result = run(graph_with(repo, recorder=FailingRecorder(engine)), initial(chunks=[CHUNK]))
    assert result["finalized"]
    assert result["research_state"] == baseline["research_state"]
    assert result["claims"] == baseline["claims"]
    assert result["post_processing_errors"] == ["persist_run: OSError"]
    assert result["distillation_jobs"] == []

def test_real_graph_runs_are_not_yet_distillable(engine, repo):
    """Honest limitation: discovery/acquisition are stubs that emit no TOOL_CALL events, so a
    real graph run has no successful research action for distillation to learn from."""
    run(graph_with(repo, recorder=RunRecorder(engine)),
        initial(chunks=[CHUNK], decisions=[{"field_key": PRIMARY, "decision": "APPROVED"}]))
    services = build_procedure_services(app_settings(eligibility_min_resolved_ratio=0.1),
                                        engine, embedder=FakeEmbedder())
    results = services.worker.run_once(limit=20)
    withdrawal = [r for r in results if any("successful research action" in x
                                            for x in r.reasons)]
    assert withdrawal and withdrawal[0].outcome is JobOutcome.INELIGIBLE

# --- telemetry ------------------------------------------------------------------------

def test_plan_and_finalize_traces(engine, repo, spans):
    seeded(repo)
    run(graph_with(repo, recorder=RunRecorder(engine)), initial(chunks=[CHUNK]))
    finished = spans.get_finished_spans()

    def parent(name):
        span = next(s for s in finished if s.name == name)
        return next(s.name for s in finished if s.context.span_id == span.parent.span_id)

    assert parent("procedure_memory.retrieve") == "graph.plan"
    assert parent("procedure_memory.filter") == "procedure_memory.retrieve"
    assert parent("trajectory.persist") == "graph.finalize"
    assert parent("distillation.enqueue") == "graph.finalize"
    plan = next(s for s in finished if s.name == "graph.plan")
    assert plan.attributes["procedure.selected_count"] == 1

# --- API ------------------------------------------------------------------------------

def test_procedure_api_lifecycle_and_provenance(engine, repo):
    services = build_procedure_services(app_settings(), engine, embedder=FakeEmbedder())
    waiting = seed_version(repo, recipe(), status=ProcedureStatus.AWAITING_APPROVAL)
    client = TestClient(create_app(procedures=services))
    listed = client.get("/procedures", params={"status": "AWAITING_APPROVAL"}).json()
    assert [v["procedure_version_id"] for v in listed] == [waiting]
    origins = client.get(f"/procedures/versions/{waiting}/origins").json()
    assert origins[0]["contribution_type"] == "CREATED" and origins[0]["run_id"]
    assert client.post(f"/procedures/versions/{waiting}/approve",
                       json={"reviewer": "hydrologist"}).json() == {"ok": True}
    assert repo.get_version(waiting).status == ProcedureStatus.ACTIVE
    assert client.post(f"/procedures/versions/{waiting}/approve",
                       json={"reviewer": "again"}).status_code == 409
    assert client.post(f"/procedures/versions/{waiting}/deprecate").status_code == 200
    stats = client.get("/procedures/stats").json()
    assert stats["versions_by_status"] == {"DEPRECATED": 1}
    assert client.get("/procedures/versions/missing/origins").status_code == 404

def test_procedure_api_disabled_by_default():
    client = TestClient(create_app())
    assert client.get("/procedures").status_code == 404
