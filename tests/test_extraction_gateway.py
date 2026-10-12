import asyncio
import pytest
from llm_helpers import Server, chat_json, error, make_llm, vt_settings
from water_source_research.agents.graph import build_graph
from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.core.config import Settings
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.llm.base import LLMError, LLMErrorKind
from water_source_research.llm.extraction import (
    StructuredExtractionGateway, build_extraction_gateway,
)
from water_source_research.llm.gateway import UnconfiguredGateway
from water_source_research.schemas.common import FieldResolutionStatus, ValueOrigin, ValueStatus
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)

FIELDS = load_field_definitions()
UTILITY = EvidenceChunk(
    chunk_id="util", url="https://toronto.ca/water", title="Toronto Water", source_type="UTILITY",
    text="Toronto Water treats drinking water drawn from Lake Ontario, a surface water source. "
         "The utility serves 3.6 million people.")
NEWS = EvidenceChunk(
    chunk_id="news", url="https://example.org/news", title="News", source_type="OTHER",
    text="Groundwater wells supply some rural areas of Ontario.")
INTAKE = EvidenceChunk(
    chunk_id="intake", url="https://toronto.ca/intake", title="Intakes", source_type="UTILITY",
    text="The R.C. Harris intake is located at latitude 43.68 north, longitude -79.28.")

def bundle(field_key, chunks=(UTILITY, NEWS)):
    state = initialize_city_state("Toronto", "Canada")
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field_key]), state,
                                  list(chunks), set())

def claim(value, *citations, status="KNOWN", origin="REPORTED", confidence=0.9):
    return {"value": value, "value_status": status, "value_origin": origin,
            "confidence": confidence,
            "citations": [{"source_id": s, "quote": q} for s, q in citations]}

def gateway(server, **llm):
    return StructuredExtractionGateway(
        make_llm(server, vt_settings(**llm), task="extract",
                 prompt_version=EXTRACTION_PROMPT_VERSION), FIELDS)

def extract(gw, field_key, chunks=(UTILITY, NEWS)):
    return asyncio.run(gw.extract(bundle(field_key, chunks), run_id="run-1",
                                  field_key=field_key))

# --- accepted claims --------------------------------------------------------------------

def test_grounded_enum_claim_becomes_field_claim_with_evidence():
    server = Server(chat_json({"claims": [claim("Surface", ("S1", "a surface water source"))]}))
    [c] = extract(gateway(server), "water_type")
    assert (c.field_key, c.value, c.value_status, c.value_origin, c.confidence) == (
        "water_type", "Surface", ValueStatus.KNOWN, ValueOrigin.REPORTED, 0.9)
    [ref] = c.evidence
    assert (ref.url, ref.title, ref.passage) == ("https://toronto.ca/water", "Toronto Water",
                                                 "a surface water source")

def test_request_carries_bundle_and_restricts_citations_to_field_sources():
    server = Server(chat_json({"claims": []}))
    extract(gateway(server), "water_type")
    [body] = server.bodies()
    user = body["messages"][1]["content"]
    assert "## Evidence" in user and "Lake Ontario" in user
    assert "Cite only these sources: S1, S2." in user
    schema = body["response_format"]["json_schema"]["schema"]
    defs = schema["$defs"]
    assert defs["Citation"]["properties"]["source_id"]["enum"] == ["S1", "S2"]
    value = defs["Claim"]["properties"]["value"]["anyOf"][0]
    assert value["enum"] == ["Surface", "Groundwater", "Reclaimed", "Desalination"]

def test_number_field_is_typed():
    server = Server(chat_json({"claims": [claim(3.6, ("S1", "serves 3.6 million people"))]}))
    [c] = extract(gateway(server), "population_served_millions", [UTILITY])
    assert c.value == 3.6

def test_quote_match_ignores_case_whitespace_and_typographic_quotes():
    server = Server(chat_json({"claims": [
        claim("Surface", ("S1", "A   SURFACE water\nsource"))]}))
    assert len(extract(gateway(server), "water_type")) == 1

def test_not_applicable_claim_has_no_value():
    server = Server(chat_json({"claims": [
        claim(None, ("S2", "Groundwater wells"), status="NOT_APPLICABLE")]}))
    [c] = extract(gateway(server), "water_type")
    assert c.value is None and c.value_status is ValueStatus.NOT_APPLICABLE

def test_no_claims_means_no_evidence():
    assert extract(gateway(Server(chat_json({"claims": []}))), "water_type") == []

# --- grounding --------------------------------------------------------------------------

def test_ungrounded_citations_are_dropped_and_claims_without_any_are_rejected(spans):
    server = Server(chat_json({"claims": [
        claim("Surface", ("S1", "a surface water source"), ("S1", "Lake Superior")),
        claim("Groundwater", ("S2", "groundwater supplies all of Toronto")),
        claim("Groundwater", ("S1", "Groundwater wells supply")),  # quote is from S2, not S1
    ]}))
    [c] = extract(gateway(server), "water_type")
    assert c.value == "Surface"
    assert [r.passage for r in c.evidence] == ["a surface water source"]
    [span] = [s for s in spans.get_finished_spans() if s.name == "llm.extract"]
    assert span.attributes["extraction.claims_returned"] == 3
    assert span.attributes["extraction.claims_accepted"] == 1
    assert span.attributes["extraction.claims_rejected"] == 2
    assert span.attributes["extraction.citations_rejected"] == 3

# --- schema enforcement -----------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    claim("Lake", ("S1", "a surface water source")),                  # not an allowed value
    claim("Surface", ("S9", "a surface water source")),               # unknown source id
    claim(None, ("S1", "a surface water source")),                    # KNOWN without value
    claim("Surface", ("S1", "a surface water source"), confidence=1.5),
    claim("Surface"),                                                  # no citation
    {**claim("Surface", ("S1", "x")), "notes": "extra"},               # extra field
])
def test_invalid_claims_are_repaired_or_yield_nothing(bad):
    good = claim("Surface", ("S1", "a surface water source"))
    repaired = Server(chat_json({"claims": [bad]}), chat_json({"claims": [good]}))
    assert [c.value for c in extract(gateway(repaired), "water_type")] == ["Surface"]
    assert len(repaired.requests) == 2

    never = Server(chat_json({"claims": [bad]}))
    assert extract(gateway(never, structured_max_attempts=1), "water_type") == []
    assert len(never.requests) == 2  # bounded: first try + 1 repair, then give up

def test_coordinates_are_range_checked():
    server = Server(chat_json({"claims": [claim(123.0, ("S1", "latitude 43.68 north"))]}),
                    chat_json({"claims": [claim(43.68, ("S1", "latitude 43.68 north"))]}))
    [c] = extract(gateway(server), "pod_latitude", [INTAKE])
    assert c.value == 43.68 and len(server.requests) == 2

# --- control flow ---------------------------------------------------------------------

def test_bundle_without_evidence_makes_no_call():
    server = Server(chat_json({"claims": []}))
    assert extract(gateway(server), "water_type", chunks=()) == []
    assert server.requests == []

def test_provider_errors_propagate_for_queue_retry():
    with pytest.raises(LLMError) as exc:
        extract(gateway(Server(error(401))), "water_type")
    assert exc.value.kind is LLMErrorKind.AUTH

def test_identical_bundles_are_served_from_cache():
    server = Server(chat_json({"claims": [claim("Surface", ("S1", "a surface water source"))]}))
    gw = gateway(server)
    assert extract(gw, "water_type") == extract(gw, "water_type")
    assert len(server.requests) == 1

def test_graph_runs_extraction_through_the_gateway():
    server = Server(chat_json({"claims": [claim("Surface", ("S1", "a surface water source"))]}))
    research = initialize_city_state("Toronto", "Canada")
    result = asyncio.run(build_graph(gateway=gateway(server)).ainvoke({
        "run_id": "run-1", "city_name": "Toronto", "country": "Canada",
        "research_state": research.model_dump(mode="json"),
        "unresolved_fields": ["water_type"],
        "evidence_chunks": [UTILITY.model_dump(mode="json"), NEWS.model_dump(mode="json")],
    }))
    fs = result["research_state"]["fields"]["water_type"]
    assert fs["status"] == FieldResolutionStatus.CANDIDATE
    assert fs["candidate_claims"][0]["value"] == "Surface"
    assert fs["candidate_claims"][0]["evidence"][0]["url"] == "https://toronto.ca/water"
    assert result["fields_needing_evidence"] == []
    assert len(server.requests) == 1

# --- wiring -----------------------------------------------------------------------------

def test_build_extraction_gateway_uses_the_configured_provider():
    gw = build_extraction_gateway(vt_settings(), FIELDS)
    assert isinstance(gw, StructuredExtractionGateway)
    assert (gw.llm.provider, gw.llm.task, gw.llm.prompt_version) == (
        "vt", "extract", EXTRACTION_PROMPT_VERSION)

def test_build_extraction_gateway_without_credentials_is_unconfigured():
    s = Settings(_env_file=None, vt_llm_base_url="", vt_llm_api_key="", vt_llm_model="",
                 openai_api_key="")
    assert isinstance(build_extraction_gateway(s, FIELDS), UnconfiguredGateway)
