"""Deterministic end-to-end slice: discover -> acquire -> chunk -> context -> VT extract ->
validate -> review -> finalize, with mocked discovery, website and VT endpoint."""

import asyncio
from urllib.parse import urlsplit
import pytest
from acquisition_helpers import html_page, make_acquisition
from discovery_helpers import web
from llm_helpers import chat_json, vt_settings
from slice_helpers import (
    BLOG_URL, DOWN_URL, PDF_URL, TARGETS, UTIL_URL, ModelScript, RoundDiscovery, initial_state,
    make_discovery, make_gateway, make_site, malformed,
)
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import Settings
from water_source_research.llm.extraction import build_extraction_gateway
from water_source_research.llm.gateway import UnconfiguredGateway
from water_source_research.reporting.run_views import field_provenance
from water_source_research.services.field_state import (
    load_field_definitions, select_fields, unresolved_fields,
)

def run(graph, state):
    return asyncio.run(graph.ainvoke(state))

def slice_graph(script=None, site=None, discovery=None, rounds=2, **kw):
    gateway, server = make_gateway(script or ModelScript(), structured_max_attempts=1)
    site = site or make_site()
    graph = build_graph(gateway=gateway, discovery=discovery or make_discovery(),
                        acquisition=make_acquisition(site), max_discovery_rounds=rounds, **kw)
    return graph, site, server

def fields_of(result):
    return result["research_state"]["fields"]

# --- 1-3, 11: the success path --------------------------------------------------------------

def test_full_slice_three_fields_with_provenance():
    graph, site, server = slice_graph()
    result = run(graph, initial_state())

    docs = {d["requested_url"]: d for d in result["acquired_documents"]}
    assert docs[UTIL_URL]["status"] == "OK"
    assert docs[UTIL_URL]["acquisition_method"] == "DIRECT_HTTP"
    assert docs[PDF_URL]["status"] == "OK"
    assert docs[PDF_URL]["acquisition_method"] == "DIRECT_HTTP_PDF"
    assert docs[PDF_URL]["page_count"] == 2 and docs[PDF_URL]["source_date"] == "2024-03-15"
    assert docs[DOWN_URL]["status"] == "FAILED"  # one failure doesn't stop the run
    # the tracking-param duplicate of UTIL_URL was never fetched twice
    assert site.fetched().count(UTIL_URL) == 1
    assert not any("utm_source" in u for u in site.fetched())

    chunks = result["evidence_chunks"]
    assert {c["url"] for c in chunks} >= {UTIL_URL, PDF_URL}
    assert {c["page_number"] for c in chunks if c["url"] == PDF_URL} == {1, 2}

    f = fields_of(result)
    assert all(f[k]["status"] == "CANDIDATE" for k in TARGETS)  # never RESOLVED by the LLM
    assert all(f[k]["selected_claim"] is None for k in TARGETS)
    [utility] = f["utility_name"]["candidate_claims"]
    assert utility["value"] == "Toronto Water"
    assert utility["evidence"][0]["url"] == UTIL_URL
    assert "City of Toronto division" in utility["evidence"][0]["passage"]

    sources = f["water_source"]["candidate_claims"]
    assert sorted(c["value"] for c in sources) == ["Groundwater wells (Toronto Island)",
                                                   "Lake Ontario"]  # kept distinct
    [withdrawal] = f["total_city_water_withdrawals_m3_day"]["candidate_claims"]
    assert withdrawal["value"] == 980000
    assert withdrawal["evidence"][0]["url"] == PDF_URL
    assert withdrawal["evidence"][0]["page_number"] == 2
    assert withdrawal["evidence"][0]["retrieved_at"]

    # Other fields were never researched.
    assert f["water_type"]["status"] == "UNRESOLVED" and not f["water_type"]["candidate_claims"]
    assert result["fields_needing_evidence"] == []
    assert result["discovery_rounds"] == 1
    assert result["finalized"]

    rows = {r["field_key"]: r for r in field_provenance(result, load_field_definitions())}
    w = rows["total_city_water_withdrawals_m3_day"]
    assert w["review_required"] and w["researched"] and w["status"] == "CANDIDATE"
    cite = w["candidates"][0]["citations"][0]
    assert cite["source_type"] and cite["acquisition_method"] == "DIRECT_HTTP_PDF"
    assert cite["source_date"] == "2024-03-15" and cite["page_number"] == 2
    assert not rows["water_type"]["researched"] and not rows["water_type"]["review_required"]

    # the multi-valued instruction reached the model for water_source only
    script = server.responses[0]
    ws_prompt = next(p for f_, p in zip(script.fields, script.prompts, strict=True)
                     if f_ == "water_source")
    assert "several distinct values" in ws_prompt
    assert "SNIPPET" not in " ".join(script.prompts)

def test_trusted_events_for_acquisition():
    graph, _, _ = slice_graph()
    result = run(graph, initial_state())
    calls = [e for e in result["events"] if e["kind"] == "TOOL_CALL" and e["node"] == "acquire"]
    fetches = [e for e in calls if e["capability"] == "static_document_fetch"]
    assert {e["result"] for e in fetches} == {"SUCCESS", "FAILURE"}
    assert any(e["capability"] == "document_parsing" and e["provider"] == "pdfium"
               for e in calls)
    assert all("?" not in (e["attrs"].get("url") or "") for e in calls)

# --- 7-8: malformed and hallucinated model output ----------------------------------------

def test_malformed_model_output_yields_no_claims_and_review():
    script = ModelScript(override={"utility_name": malformed})
    graph, _, _ = slice_graph(script)
    result = run(graph, initial_state())
    f = fields_of(result)
    assert f["utility_name"]["status"] == "UNRESOLVED"
    assert f["utility_name"]["candidate_claims"] == []
    assert result["fields_needing_evidence"] == ["utility_name"]
    assert result["discovery_rounds"] == 2  # sent back to discovery once
    assert result["needs_human_review"]
    assert "utility_name" in result["review_reason"]
    assert f["water_source"]["status"] == "CANDIDATE"  # other fields unaffected

def test_hallucinated_quote_and_unknown_source_rejected():
    def invented_quote(prompt):
        return chat_json({"claims": [{
            "value": "Toronto Hydro Water", "value_status": "KNOWN", "value_origin": "REPORTED",
            "confidence": 0.95,
            "citations": [{"source_id": "S1", "quote": "Toronto Hydro Water operates all "
                                                        "drinking water services"}]}]})

    def unknown_source(prompt):
        return chat_json({"claims": [{
            "value": 1, "value_status": "KNOWN", "value_origin": "REPORTED", "confidence": 0.9,
            "citations": [{"source_id": "S99", "quote": "anything"}]}]})

    script = ModelScript(override={"utility_name": invented_quote,
                                   "total_city_water_withdrawals_m3_day": unknown_source})
    graph, _, _ = slice_graph(script)
    result = run(graph, initial_state())
    f = fields_of(result)
    for key in ("utility_name", "total_city_water_withdrawals_m3_day"):
        assert f[key]["candidate_claims"] == [] and f[key]["status"] == "UNRESOLVED"
    assert sorted(result["fields_needing_evidence"]) == [
        "total_city_water_withdrawals_m3_day", "utility_name"]
    assert result["needs_human_review"]
    assert not any(c["value"] == "Toronto Hydro Water" for c in result["claims"])

# --- 9, 12: VT credentials and no OpenAI ------------------------------------------------------

def test_missing_vt_credentials_never_fall_back_to_openai():
    settings = Settings(_env_file=None, llm_provider="vt", vt_llm_base_url="",
                        vt_llm_api_key="", vt_llm_model="", openai_api_key="sk-paid-key")
    gateway = build_extraction_gateway(settings)
    assert isinstance(gateway, UnconfiguredGateway)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=1)
    with pytest.raises(NotImplementedError, match="No LLM gateway configured"):
        run(graph, initial_state())

def test_vt_mode_never_invokes_openai(monkeypatch):
    from water_source_research.llm import openai_reasoner

    def forbidden(*args, **kwargs):
        raise AssertionError("OpenAI backend must not be constructed in VT mode")

    monkeypatch.setattr(openai_reasoner.OpenAIResponsesBackend, "__init__", forbidden)
    settings = vt_settings(structured_max_attempts=1).model_copy(
        update={"openai_api_key": "sk-paid-key-present"})
    script = ModelScript()
    gateway, server = make_gateway(script, settings)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=1)
    result = run(graph, initial_state())
    assert server.requests and {urlsplit(str(r.url)).hostname for r in server.requests} == {
        "vt.test"}
    assert {r.url.path for r in server.requests} == {"/api/v1/chat/completions"}
    assert all(r.headers["authorization"] == "Bearer sk-vt-test" for r in server.requests)
    assert fields_of(result)["utility_name"]["status"] == "CANDIDATE"

# --- 10: rediscovery ---------------------------------------------------------------------------

def test_rediscovery_acquires_only_new_documents():
    blog = web("Best hikes near Toronto", BLOG_URL)
    util = web("Tap water in Toronto", UTIL_URL)
    discovery = RoundDiscovery([[blog], [blog, util]])
    graph, site, _ = slice_graph(discovery=discovery, rounds=3,
                                 script=ModelScript(answers={"utility_name": [
                                     {"value": "Toronto Water", "confidence": 0.9,
                                      "quote": "Toronto Water is the City of Toronto division"}
                                 ]}))
    result = run(graph, initial_state(["utility_name"]))
    assert result["discovery_rounds"] == 2
    assert site.fetched().count(BLOG_URL) == 1  # not re-fetched in round 2
    rounds = {d["requested_url"]: d["round"] for d in result["acquired_documents"]}
    assert rounds == {BLOG_URL: 1, UTIL_URL: 2}
    assert fields_of(result)["utility_name"]["status"] == "CANDIDATE"
    assert not result["needs_human_review"]

def test_nothing_acquirable_ends_in_review_not_failure():
    site = make_site().status(UTIL_URL, 403).status(PDF_URL, 404)
    graph, _, _ = slice_graph(site=site, rounds=2)
    result = run(graph, initial_state())
    assert all(fields_of(result)[k]["status"] == "UNRESOLVED" for k in TARGETS)
    assert result["needs_human_review"] and result["finalized"]
    auth = [d for d in result["acquired_documents"] if d["error_kind"] == "AUTH_REQUIRED"]
    assert [d["requested_url"] for d in auth] == [UTIL_URL]

def test_empty_documents_produce_no_evidence():
    site = make_site().html(UTIL_URL, b"<html><head><title>x</title></head><body></body></html>")
    site.status(PDF_URL, 404)
    graph, _, _ = slice_graph(site=site, rounds=1)
    result = run(graph, initial_state(["utility_name"]))
    docs = {d["requested_url"]: d["status"] for d in result["acquired_documents"]}
    assert docs[UTIL_URL] == "EMPTY"
    assert not any(c["url"] == UTIL_URL for c in result["evidence_chunks"])
    assert fields_of(result)["utility_name"]["status"] == "UNRESOLVED"

# --- 11: review semantics -------------------------------------------------------------------

def test_only_reviewer_approval_resolves_a_field():
    graph, _, _ = slice_graph()
    state = initial_state(review_decisions=[
        {"field_key": "utility_name", "decision": "APPROVED"},
        {"field_key": "water_source", "decision": "REJECTED"}])
    result = run(graph, state)
    f = fields_of(result)
    assert f["utility_name"]["status"] == "RESOLVED"
    assert f["utility_name"]["selected_claim"]["value"] == "Toronto Water"
    assert f["water_source"]["status"] == "UNRESOLVED"
    assert f["total_city_water_withdrawals_m3_day"]["status"] == "CANDIDATE"
    rows = {r["field_key"]: r for r in field_provenance(result, load_field_definitions())}
    assert not rows["utility_name"]["review_required"]
    assert rows["utility_name"]["citations"][0]["url"] == UTIL_URL
    assert rows["total_city_water_withdrawals_m3_day"]["review_required"]

# --- field subset ---------------------------------------------------------------------------

def test_field_subset_validation():
    assert select_fields(None) is None
    assert select_fields(["water_source", "utility_name", "water_source"]) == [
        "water_source", "utility_name"]
    with pytest.raises(ValueError, match="nonexistent_field"):
        select_fields(["utility_name", "nonexistent_field"])
    assert len(load_field_definitions()) == 32
    assert load_field_definitions()["water_source"].multi_valued

def test_subset_limits_research_and_discovery_queries():
    graph, _, server = slice_graph()
    result = run(graph, initial_state(["utility_name"]))
    script = server.responses[0]
    assert set(script.fields) == {"utility_name"}
    assert result["unresolved_fields"] == ["utility_name"]
    assert unresolved_fields(initial_state_research(), ["utility_name"]) == ["utility_name"]

def initial_state_research():
    from water_source_research.services.field_state import initialize_city_state
    return initialize_city_state("Toronto", "Canada")

def test_html_only_site_still_works_without_pdf():
    site = make_site().status(PDF_URL, 404).html(
        UTIL_URL, html_page("Toronto Water", "Toronto Water is the City of Toronto division "
                            "that provides drinking water to residents."))
    graph, _, _ = slice_graph(site=site, rounds=1)
    result = run(graph, initial_state())
    f = fields_of(result)
    assert f["utility_name"]["status"] == "CANDIDATE"
    assert f["total_city_water_withdrawals_m3_day"]["status"] == "UNRESOLVED"
    assert "total_city_water_withdrawals_m3_day" in result["fields_needing_evidence"]

# --- LLM usage accounting -------------------------------------------------------------------

def test_real_model_requests_are_recorded_per_extraction():
    script = ModelScript()
    gateway, server = make_gateway(script, structured_max_attempts=1)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=1)
    result = run(graph, initial_state())
    usage = [e for e in result["events"] if e["node"] == "extract" and e["kind"] == "NODE"
             and e["attrs"].get("bundle_status") == "READY"]
    assert {e["field_key"] for e in usage} == set(TARGETS)
    assert sum(e["attrs"]["llm_requests"] for e in usage) == len(server.requests)
    assert all(e["attrs"]["model"] == "vt:gpt-oss-120b" and e["attrs"]["prompt_tokens"] > 0
               for e in usage)

    # Same process, same prompts: served from the response cache -> 0 real requests.
    again = run(build_graph(gateway=gateway, discovery=make_discovery(),
                            acquisition=make_acquisition(make_site()), max_discovery_rounds=1),
                initial_state(run_id="run-2"))
    cached = [e for e in again["events"] if e["node"] == "extract" and e["kind"] == "NODE"
              and e["attrs"].get("bundle_status") == "READY"]
    assert sum(e["attrs"]["llm_requests"] for e in cached) == 0
    assert all(e["attrs"]["cache_hits"] == 1 for e in cached)
    assert len(server.requests) == sum(e["attrs"]["llm_requests"] for e in usage)
