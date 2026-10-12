"""Source discovery against the real providers (OpenAlex, Crossref, Semantic Scholar, SearXNG).

Assertions are about shape and invariants (results parse, are typed, deduped, ranked, and
reported as trusted events), not specific titles, since live data changes.
"""

import asyncio
import pytest
from water_source_research.agents.graph import build_graph
from water_source_research.discovery.crossref import CrossrefDiscovery
from water_source_research.discovery.http import ProviderError, ProviderErrorKind
from water_source_research.discovery.openalex import OpenAlexDiscovery
from water_source_research.discovery.queries import build_queries
from water_source_research.discovery.searxng import SearXNGDiscovery
from water_source_research.discovery.semantic_scholar import SemanticScholarDiscovery
from water_source_research.discovery.service import build_discovery_service
from water_source_research.schemas.events import EventKind
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions, unresolved_fields,
)

QUERY = "Toronto drinking water supply Lake Ontario"
FIELDS = load_field_definitions()
KNOWN_DOI = "10.1038/nature14539"  # LeCun, Bengio & Hinton (2015) "Deep learning", Nature

def run(coro):
    return asyncio.run(coro)

def assert_results(results, provider):
    assert results, f"{provider} returned nothing for {QUERY!r}"
    for r in results:
        assert r.provider == provider and r.title and r.url.startswith("http")
        assert r.source_type
    assert [r.rank for r in results] == list(range(1, len(results) + 1))

def test_openalex_search(live_settings):
    results = run(OpenAlexDiscovery(live_settings.openalex_api_key,
                                    live_settings.discovery).search(QUERY, limit=5))
    assert_results(results, "openalex")
    assert all(r.source_type == "ACADEMIC" for r in results)
    assert any(r.doi for r in results)

def test_crossref_search_and_doi_verification(live_settings):
    crossref = CrossrefDiscovery(live_settings.crossref_mailto, live_settings.discovery)
    assert_results(run(crossref.search(QUERY, limit=5)), "crossref")
    record = run(crossref.lookup_doi(f"https://doi.org/{KNOWN_DOI.upper()}"))
    assert record is not None and record.doi == KNOWN_DOI
    assert "deep learning" in record.title.lower()
    assert run(crossref.lookup_doi("10.9999/definitely-not-a-registered-doi-xyz")) is None

def test_semantic_scholar_search(live_settings):
    s2 = SemanticScholarDiscovery(live_settings.semantic_scholar_api_key,
                                  live_settings.discovery)
    try:
        results = run(s2.search(QUERY, limit=5))
    except ProviderError as exc:
        if exc.kind is ProviderErrorKind.RATE_LIMITED and not live_settings.semantic_scholar_api_key:
            pytest.skip("Semantic Scholar rate-limits keyless clients; set SEMANTIC_SCHOLAR_API_KEY")
        raise
    assert_results(results, "semantic_scholar")

def test_searxng_web_search(searxng_ready):
    results = run(SearXNGDiscovery(searxng_ready.searxng_url,
                                   searxng_ready.discovery).search(QUERY, limit=8))
    assert_results(results, "searxng")

def test_discovery_service_end_to_end(live_settings):
    service = build_discovery_service(live_settings)
    queries = build_queries(city="Toronto", country="Canada", utility=None,
                            field_keys=["utility_name", "water_source"], fields=FIELDS,
                            max_per_kind=2)
    outcome = run(service.discover(queries, city="Toronto", country="Canada"))

    assert outcome.candidates, outcome.provider_status
    assert outcome.provider_status.get("openalex") == "ok", outcome.provider_status
    urls = [c.url for c in outcome.candidates]
    assert len(urls) == len(set(urls)), "candidates must be deduplicated"
    scores = [c.score for c in outcome.candidates]
    assert scores == sorted(scores, reverse=True), "candidates must be ranked"
    # Every provider call is accounted for; failures are isolated, never raised.
    assert {c.result for c in outcome.calls} <= {"SUCCESS", "NO_RESULT", "FAILURE"}
    assert any(c.result == "SUCCESS" for c in outcome.calls)
    verified = [c for c in outcome.candidates if c.doi_verified is not None]
    if live_settings.discovery.crossref_verify_top_n and any(c.doi for c in outcome.candidates):
        assert verified, "top DOIs should be checked with Crossref"

def test_graph_discover_node_against_real_providers(live_settings):
    """plan -> discover (real APIs) -> acquire (stub) -> ... -> finalize, one round."""
    state = initialize_city_state("Toronto", "Canada")
    graph = build_graph(discovery=build_discovery_service(live_settings),
                        max_discovery_rounds=1)
    result = run(graph.ainvoke({
        "run_id": "live-discovery", "city_name": "Toronto", "country": "Canada",
        "research_state": state.model_dump(mode="json"),
        "unresolved_fields": unresolved_fields(state),
    }))
    assert result["finalized"] and result["discovery_rounds"] == 1
    assert result["discovery_candidates"], result["discovery_provider_status"]
    assert result["candidate_urls"] == [c["url"] for c in result["discovery_candidates"]]
    tool_calls = [e for e in result["events"] if e["kind"] == EventKind.TOOL_CALL]
    assert tool_calls and {e["node"] for e in tool_calls} == {"discover"}
    assert any(e["result"] == "SUCCESS" for e in tool_calls)
