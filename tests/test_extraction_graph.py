import asyncio
import pytest
from sqlalchemy import create_engine
from water_source_research.agents import graph as graph_module
from water_source_research.agents.graph import build_graph
from water_source_research.db.session import init_db
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import EvidenceChunk, FieldClaim
from water_source_research.context_manager import (
    ContextBundleStore, ContextPurpose, ContextService, ContextStatus,
)
from water_source_research.services.field_state import initialize_city_state

UTILITY_CHUNK = EvidenceChunk(
    chunk_id="util", url="https://toronto.ca/water", title="Toronto Water",
    source_type="UTILITY",
    text="Toronto Water is the municipal utility responsible for drinking water in Toronto.",
)

class FakeGateway:
    """Stands in for the LLM gateway: cites the first source in the bundle."""

    def __init__(self, value="Toronto Water", return_claims=True):
        self.value = value
        self.return_claims = return_claims
        self.calls = []

    async def extract(self, bundle, *, run_id, field_key):
        self.calls.append((run_id, field_key, bundle))
        if not self.return_claims:
            return []
        return [FieldClaim(field_key=field_key, value=self.value, confidence=0.9,
                           evidence=[bundle.evidence_ref("S1")])]

def initial_state(fields, chunks=(), run_id="run-1"):
    research = initialize_city_state("Toronto", "Canada")
    return {
        "run_id": run_id,
        "city_name": "Toronto",
        "country": "Canada",
        "research_state": research.model_dump(mode="json"),
        "unresolved_fields": fields,
        "evidence_chunks": [c.model_dump(mode="json") for c in chunks],
    }

def run(graph, state):
    return asyncio.run(graph.ainvoke(state))

# --- success path ---------------------------------------------------------------------

def test_ready_bundle_is_passed_to_gateway_and_claim_recorded():
    gateway = FakeGateway()
    result = run(build_graph(gateway=gateway), initial_state(["utility_name"], [UTILITY_CHUNK]))

    [(run_id, field_key, bundle)] = gateway.calls
    assert (run_id, field_key) == ("run-1", "utility_name")
    assert bundle.status is ContextStatus.READY
    assert bundle.purpose == ContextPurpose.EXTRACT
    assert bundle.target_fields == ["utility_name"]

    fs = result["research_state"]["fields"]["utility_name"]
    assert fs["status"] == FieldResolutionStatus.CANDIDATE
    assert fs["candidate_claims"][0]["evidence"][0]["url"] == "https://toronto.ca/water"
    assert result["claims"][0]["value"] == "Toronto Water"
    assert result["seen_evidence"] == [["util", "utility_name"]]
    assert result["fields_needing_evidence"] == []
    assert result["discovery_rounds"] == 1
    assert not result["needs_human_review"]
    assert result["finalized"]

def test_not_needed_fields_skip_gateway_and_discovery():
    gateway = FakeGateway()
    state = initial_state(["percentage_total_population", "utility_name"], [UTILITY_CHUNK])
    state["research_state"]["fields"]["utility_name"]["status"] = "RESOLVED"
    result = run(build_graph(gateway=gateway), state)
    assert gateway.calls == []
    assert result["fields_needing_evidence"] == []
    assert result["discovery_rounds"] == 1

def test_fields_extracted_independently():
    gateway = FakeGateway()
    pop = EvidenceChunk(chunk_id="pop", url="https://stats.ca/t", source_type="GOVERNMENT",
                        text="Toronto had a population of 2.79 million residents in 2021.")
    result = run(build_graph(gateway=gateway),
                 initial_state(["utility_name", "city_population_millions"],
                               [UTILITY_CHUNK, pop]))
    assert sorted(f for _, f, _ in gateway.calls) == ["city_population_millions", "utility_name"]
    for _, field_key, bundle in gateway.calls:
        assert bundle.target_fields == [field_key]
    assert result["fields_needing_evidence"] == []

# --- NO_SUFFICIENT_EVIDENCE routing ---------------------------------------------------

def test_no_sufficient_evidence_routes_back_to_discovery_until_budget():
    gateway = FakeGateway()
    result = run(build_graph(gateway=gateway, max_discovery_rounds=3),
                 initial_state(["utility_name"]))
    assert gateway.calls == []
    assert result["discovery_rounds"] == 3
    assert result["fields_needing_evidence"] == ["utility_name"]
    assert result["needs_human_review"]
    assert "utility_name" in result["review_reason"]
    assert result["finalized"]

def test_evidence_found_on_rediscovery_is_extracted(monkeypatch):
    original = graph_module.acquire_node

    async def acquire_on_second_round(state):
        state = await original(state)
        if state["discovery_rounds"] == 2:
            state = {**state, "evidence_chunks": [UTILITY_CHUNK.model_dump(mode="json")]}
        return state

    monkeypatch.setattr(graph_module, "acquire_node", acquire_on_second_round)
    gateway = FakeGateway()
    result = run(build_graph(gateway=gateway, max_discovery_rounds=3),
                 initial_state(["utility_name"]))
    assert result["discovery_rounds"] == 2
    assert len(gateway.calls) == 1
    assert result["research_state"]["fields"]["utility_name"]["status"] == "CANDIDATE"
    assert not result["needs_human_review"]

def test_only_insufficient_fields_are_retried(monkeypatch):
    gateway = FakeGateway()
    result = run(build_graph(gateway=gateway, max_discovery_rounds=2),
                 initial_state(["utility_name", "reservoir_names"], [UTILITY_CHUNK]))
    # utility_name succeeds in round 1; only reservoir_names goes around again.
    assert [f for _, f, _ in gateway.calls] == ["utility_name"]
    assert result["fields_needing_evidence"] == ["reservoir_names"]
    assert result["discovery_rounds"] == 2

def test_gateway_returning_no_claims_rediscovers_without_resending_evidence():
    gateway = FakeGateway(return_claims=False)
    result = run(build_graph(gateway=gateway, max_discovery_rounds=3),
                 initial_state(["utility_name"], [UTILITY_CHUNK]))
    # The chunk was sent once; afterwards it is "seen" for this field, so later rounds
    # report NO_SUFFICIENT_EVIDENCE instead of re-sending the same evidence.
    assert len(gateway.calls) == 1
    assert result["seen_evidence"] == [["util", "utility_name"]]
    assert result["fields_needing_evidence"] == ["utility_name"]
    assert result["needs_human_review"]

def test_gateway_claim_for_wrong_field_is_rejected():
    class WrongField(FakeGateway):
        async def extract(self, bundle, *, run_id, field_key):
            return [FieldClaim(field_key="water_source", value="x", confidence=0.5)]

    with pytest.raises(ValueError, match="water_source"):
        run(build_graph(gateway=WrongField()), initial_state(["utility_name"], [UTILITY_CHUNK]))

def test_unconfigured_gateway_fails_loudly_only_when_evidence_exists():
    assert run(build_graph(max_discovery_rounds=1), initial_state(["utility_name"]))["finalized"]
    with pytest.raises(NotImplementedError):
        run(build_graph(), initial_state(["utility_name"], [UTILITY_CHUNK]))

# --- context service contract ---------------------------------------------------------

def test_context_service_rejects_mismatched_run():
    service = ContextService()
    with pytest.raises(ValueError, match="run-1"):
        asyncio.run(service.get_context(run_id="other", field_key="utility_name",
                                        operation="EXTRACT",
                                        graph_state=initial_state(["utility_name"])))

def test_persisted_bundles_reused_across_runs_with_same_inputs(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ctx.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    init_db(engine)
    service = ContextService(store=ContextBundleStore(engine))
    gateway = FakeGateway()
    graph = build_graph(gateway=gateway, context_service=service)
    first = run(graph, initial_state(["utility_name"], [UTILITY_CHUNK], run_id="a"))
    second = run(graph, initial_state(["utility_name"], [UTILITY_CHUNK], run_id="b"))
    assert first["context_bundle_ids"] == second["context_bundle_ids"]
    assert [b.cache_hit for _, _, b in gateway.calls] == [False, True]
    # Bundle reuse never short-circuits the gateway; LLM response caching is its job.
    assert len(gateway.calls) == 2
    engine.dispose()

def test_default_graph_from_plan_creates_run_state():
    result = run(build_graph(max_discovery_rounds=1),
                 {"city_name": "Toronto", "country": "Canada", "unresolved_fields": ["utility_name"]})
    assert result["run_id"]
    assert set(result["research_state"]["fields"]) >= {"utility_name"}

# --- telemetry ------------------------------------------------------------------------

PIPELINE_SPANS = [
    "context.load_state", "context.retrieval", "context.ranking", "context.dedupe",
    "context.budgeting", "context.bundle_create",
]

def test_spans_cover_every_context_phase_with_correct_nesting(spans):
    run(build_graph(gateway=FakeGateway()), initial_state(["utility_name"], [UTILITY_CHUNK]))
    finished = spans.get_finished_spans()
    by_name = {s.name: s for s in finished}
    for name in [*PIPELINE_SPANS, "context.get_context", "context_manager.build", "graph.extract"]:
        assert name in by_name, name

    def parent(name):
        pid = by_name[name].parent.span_id
        return next(s.name for s in finished if s.context.span_id == pid)

    assert parent("context.get_context") == "graph.extract"
    assert parent("context.load_state") == "context.get_context"
    assert parent("context.bundle_create") == "context.get_context"
    assert parent("context_manager.build") == "context.bundle_create"
    for phase in ("context.retrieval", "context.ranking", "context.dedupe", "context.budgeting"):
        assert parent(phase) == "context_manager.build"

    root = by_name["context.get_context"].attributes
    assert root["context.run_id"] == "run-1"
    assert root["context.field_key"] == "utility_name"
    assert root["context.operation"] == "EXTRACT"
    assert root["context.status"] == "READY"
    assert by_name["context.budgeting"].attributes["context.chunks_selected"] == 1
    assert by_name["context.retrieval"].attributes["context.chunks_considered"] == 1

def test_cache_hit_skips_retrieval_spans(tmp_path, spans):
    engine = create_engine(f"sqlite:///{tmp_path / 'ctx.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    init_db(engine)
    graph = build_graph(gateway=FakeGateway(),
                        context_service=ContextService(store=ContextBundleStore(engine)))
    run(graph, initial_state(["utility_name"], [UTILITY_CHUNK]))
    spans.clear()
    run(graph, initial_state(["utility_name"], [UTILITY_CHUNK]))
    names = {s.name for s in spans.get_finished_spans()}
    assert {"context.load_state", "context.bundle_create"} <= names
    assert not names & {"context.retrieval", "context.ranking", "context.dedupe",
                        "context.budgeting"}
    create = next(s for s in spans.get_finished_spans() if s.name == "context.bundle_create")
    assert create.attributes["context.cache_hit"] is True
    engine.dispose()

def test_insufficient_evidence_status_is_traced(spans):
    run(build_graph(gateway=FakeGateway(), max_discovery_rounds=1),
        initial_state(["utility_name"]))
    root = next(s for s in spans.get_finished_spans() if s.name == "context.get_context")
    assert root.attributes["context.status"] == ContextStatus.NO_SUFFICIENT_EVIDENCE
