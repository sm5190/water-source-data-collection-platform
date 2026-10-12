"""The LLM router, extraction gateway and embeddings against the REAL VT ARC LLM API.

Needs the VT Campus VPN (skipped with the reason otherwise). Model output varies, so the
assertions target what the pipeline guarantees: validated types, grounded citations that map
back to the evidence, provider/model telemetry, and no claim without evidence.
"""

import asyncio
import math
import pytest
from pydantic import BaseModel, ConfigDict
from water_source_research.agents.graph import build_graph
from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.discovery.queries import build_queries
from water_source_research.discovery.service import build_discovery_service
from water_source_research.llm.extraction import (
    StructuredExtractionGateway, _normalize, build_extraction_gateway,
)
from water_source_research.llm.router import build_embedding_client, build_structured_llm
from water_source_research.schemas.common import FieldResolutionStatus, ValueStatus
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)

FIELDS = load_field_definitions()
UTILITY = EvidenceChunk(
    chunk_id="live-utility", url="https://www.toronto.ca/services-payments/water-environment/",
    title="Toronto Water", source_type="UTILITY",
    text="Toronto Water is the municipal utility responsible for drinking water in Toronto. "
         "It treats drinking water drawn from Lake Ontario, a surface water source, and serves "
         "3.6 million residents and businesses in Toronto and York Region.")

def run(coro):
    return asyncio.run(coro)

def extract(gateway, field_key, chunks):
    state = initialize_city_state("Toronto", "Canada")
    bundle = ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field_key]), state,
                                    chunks, set())
    return run(gateway.extract(bundle, run_id="live", field_key=field_key))

def assert_grounded(claims, chunks):
    by_url = {c.url: _normalize(c.text) for c in chunks}
    for claim in claims:
        assert claim.evidence, "every claim must cite evidence"
        for ref in claim.evidence:
            assert ref.url in by_url, ref.url
            assert _normalize(ref.passage) in by_url[ref.url], ref.passage

@pytest.fixture(scope="module")
def gateway(vt_ready):
    gw = build_extraction_gateway(vt_ready, FIELDS)
    assert isinstance(gw, StructuredExtractionGateway)
    assert (gw.llm.provider, gw.llm.model) == ("vt", vt_ready.vt_llm_model)
    return gw

# --- router -----------------------------------------------------------------------------

class CityFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str
    country: str
    on_a_lake: bool

def test_structured_call_through_router_on_vt(vt_ready, spans):
    llm = build_structured_llm(vt_ready, task="live-test", prompt_version="live-1")
    facts = llm.structured("Answer factual questions about cities.",
                           "Which country is Toronto in, and is it on a lake?", CityFacts)
    assert facts.country.lower() == "canada" and facts.on_a_lake is True
    [span] = [s for s in spans.get_finished_spans() if s.name == "llm.call"]
    a = span.attributes
    assert (a["llm.provider"], a["llm.model"]) == ("vt", vt_ready.vt_llm_model)
    assert a["llm.attempts"] >= 1 and a["llm.latency_ms"] > 0
    assert a.get("llm.prompt_tokens", 0) > 0 and a.get("llm.completion_tokens", 0) > 0

def test_async_and_cached_calls(vt_ready):
    llm = build_structured_llm(vt_ready, task="live-test", prompt_version="live-async")
    ask = ("Answer factual questions about cities.", "Is Chicago on a lake? Country?", CityFacts)
    first = run(llm.astructured(*ask))
    assert run(llm.astructured(*ask)) == first  # second call is a cache hit

# --- extraction gateway -----------------------------------------------------------------

def test_extracts_enum_field_with_grounded_citation(gateway):
    claims = extract(gateway, "water_type", [UTILITY])
    assert claims, "VT returned no grounded claim for an explicit statement"
    assert {c.value for c in claims} == {"Surface"}
    assert all(c.value_status is ValueStatus.KNOWN for c in claims)
    assert_grounded(claims, [UTILITY])

def test_extracts_typed_number_in_the_field_unit(gateway):
    claims = extract(gateway, "population_served_millions", [UTILITY])
    assert claims, "VT returned no grounded claim for an explicit statement"
    assert any(math.isclose(c.value, 3.6, abs_tol=0.05) for c in claims), \
        [c.value for c in claims]
    assert_grounded(claims, [UTILITY])

def test_graph_extract_node_uses_vt(gateway):
    state = initialize_city_state("Toronto", "Canada")
    result = run(build_graph(gateway=gateway, max_discovery_rounds=1).ainvoke({
        "run_id": "live-graph", "city_name": "Toronto", "country": "Canada",
        "research_state": state.model_dump(mode="json"),
        "unresolved_fields": ["utility_name", "water_type"],
        "evidence_chunks": [UTILITY.model_dump(mode="json")],
    }))
    fields = result["research_state"]["fields"]
    assert fields["water_type"]["status"] == FieldResolutionStatus.CANDIDATE
    assert fields["water_type"]["candidate_claims"][0]["value"] == "Surface"
    for key in ("utility_name", "water_type"):
        for claim in fields[key]["candidate_claims"]:
            assert claim["evidence"] and claim["evidence"][0]["url"] == UTILITY.url
    assert result["finalized"]

def test_discovered_sources_feed_vt_extraction(gateway, live_settings):
    """Real discovery -> candidate titles/abstracts as evidence -> VT extraction.

    Whatever the model claims must cite one of the discovered sources verbatim.
    """
    service = build_discovery_service(live_settings)
    queries = build_queries(city="Toronto", country="Canada", utility=None,
                            field_keys=["water_source"], fields=FIELDS, max_per_kind=1)
    outcome = run(service.discover(queries, city="Toronto", country="Canada"))
    chunks = [EvidenceChunk(chunk_id=f"d{i}", url=c.url, title=c.title,
                            source_type=c.source_type,
                            text=f"{c.title}. {c.snippet or ''}".strip())
              for i, c in enumerate(outcome.candidates[:8])]
    if not chunks:
        pytest.skip(f"discovery found nothing: {outcome.provider_status}")
    claims = extract(gateway, "water_source", chunks)
    assert_grounded(claims, chunks)

# --- embeddings -------------------------------------------------------------------------

def test_vt_embeddings(vt_ready):
    client = build_embedding_client(vt_ready)
    assert client.model == vt_ready.vt_llm_embedding_model
    water, other = client.embed([
        "Annual drinking water production and withdrawals reported by the municipal utility.",
        "A recipe for chocolate cake with butter, sugar and eggs."])
    query = client.embed_query("how much water does the city withdraw each year")
    for v in (water, other, query):
        assert len(v) == 1536
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert cos(query, water) > cos(query, other)
