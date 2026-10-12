import asyncio
import pytest
from discovery_helpers import (
    CROSSREF_WORK, OPENALEX_WORKS, S2_SEARCH, SEARXNG_SEARCH, SETTINGS, Recorder, http_kwargs,
    ok, timeout,
)
from water_source_research.discovery.crossref import CrossrefDiscovery
from water_source_research.discovery.http import ProviderError, ProviderErrorKind
from water_source_research.discovery.openalex import OpenAlexDiscovery
from water_source_research.discovery.searxng import SearXNGDiscovery
from water_source_research.discovery.semantic_scholar import SemanticScholarDiscovery

def run(coro):
    return asyncio.run(coro)

# --- OpenAlex -------------------------------------------------------------------------

def test_openalex_normalizes_work_and_sends_key():
    handler = Recorder(ok(OPENALEX_WORKS))
    [r] = run(OpenAlexDiscovery("oa-key", SETTINGS, **http_kwargs(handler)).search("toronto", 5))
    params = handler.requests[0].url.params
    assert params["api_key"] == "oa-key" and params["search"] == "toronto"
    assert params["per-page"] == "5" and "doi" in params["select"]
    assert r.provider == "openalex" and r.providers == ["openalex"] and r.rank == 1
    assert r.doi == "10.1016/j.watres.2019.01.001"
    assert r.url == "https://www.sciencedirect.com/science/article/1"  # original landing URL
    assert r.published_date == "2019-03-01" and r.venue == "Water Research"
    assert r.authors == ["A. Smith", "B. Jones"]
    assert r.provider_ids == {"openalex": "W2741809807"}
    assert r.snippet == "Toronto draws from Lake Ontario" and r.query == "toronto"
    assert r.source_type == "ACADEMIC"

def test_openalex_keyless_omits_api_key():
    handler = Recorder(ok({"results": []}))
    assert run(OpenAlexDiscovery("", SETTINGS, **http_kwargs(handler)).search("x")) == []
    assert "api_key" not in handler.requests[0].url.params

@pytest.mark.parametrize("payload", [{"results": None}, {"meta": {}}, {"results": []}])
def test_openalex_empty_results(payload):
    assert run(OpenAlexDiscovery("", SETTINGS, **http_kwargs(Recorder(ok(payload))))
               .search("x")) == []

def test_openalex_malformed_payload_raises_malformed():
    provider = OpenAlexDiscovery("", SETTINGS, **http_kwargs(Recorder(ok(["not", "an", "obj"]))))
    with pytest.raises(ProviderError) as err:
        run(provider.search("x"))
    assert err.value.kind is ProviderErrorKind.MALFORMED

def test_openalex_skips_items_without_title_or_url():
    payload = {"results": [{"id": None, "display_name": "No link"}, {"display_name": None},
                           "junk", OPENALEX_WORKS["results"][0]]}
    results = run(OpenAlexDiscovery("", SETTINGS, **http_kwargs(Recorder(ok(payload))))
                  .search("x"))
    assert [r.rank for r in results] == [1] and results[0].doi

# --- Semantic Scholar -----------------------------------------------------------------

def test_semantic_scholar_normalizes_paper_and_sends_header():
    handler = Recorder(ok(S2_SEARCH))
    [r] = run(SemanticScholarDiscovery("s2-key", SETTINGS, **http_kwargs(handler))
              .search("toronto water"))
    req = handler.requests[0]
    assert req.headers["x-api-key"] == "s2-key"
    assert req.url.path == "/graph/v1/paper/search" and "externalIds" in req.url.params["fields"]
    assert r.doi == "10.1016/j.watres.2019.01.001" and r.published_date == "2019"
    assert r.url == "https://www.semanticscholar.org/paper/649def34"
    assert r.provider_ids == {"semantic_scholar": "649def34f8be52c8b66281af98ae884c09aef38b"}
    assert r.authors == ["A. Smith"] and r.venue == "Water Research"

def test_semantic_scholar_without_key_sends_no_header():
    handler = Recorder(ok({"total": 0, "offset": 0}))  # API omits "data" when empty
    assert run(SemanticScholarDiscovery("", SETTINGS, **http_kwargs(handler)).search("x")) == []
    assert "x-api-key" not in handler.requests[0].headers

def test_semantic_scholar_429_exhausted():
    provider = SemanticScholarDiscovery("", SETTINGS, **http_kwargs(Recorder(ok({}, 429))))
    with pytest.raises(ProviderError) as err:
        run(provider.search("x"))
    assert err.value.kind is ProviderErrorKind.RATE_LIMITED

def test_semantic_scholar_related_papers():
    handler = Recorder(ok({"recommendedPapers": S2_SEARCH["data"]}))
    [r] = run(SemanticScholarDiscovery("", SETTINGS, **http_kwargs(handler))
              .related("649def34", 3))
    assert handler.requests[0].url.path == "/recommendations/v1/papers/forpaper/649def34"
    assert r.query == "related:649def34"
    unknown = SemanticScholarDiscovery("", SETTINGS, **http_kwargs(Recorder(ok({}, 404))))
    assert run(unknown.related("missing")) == []

# --- Crossref -------------------------------------------------------------------------

def test_crossref_search_uses_polite_pool_and_normalizes():
    handler = Recorder(ok({"status": "ok", "message": {"items": [CROSSREF_WORK]}}))
    [r] = run(CrossrefDiscovery("me@example.org", SETTINGS, **http_kwargs(handler))
              .search("toronto water"))
    req = handler.requests[0]
    assert req.url.params["mailto"] == "me@example.org"
    assert "mailto:me@example.org" in req.headers["user-agent"]
    assert r.published_date == "2019-02-14" and r.venue == "Water Research"
    assert r.authors == ["Alice Smith"]
    assert r.snippet == "Toronto draws from Lake Ontario ."

def test_crossref_without_mailto_sends_no_mailto():
    handler = Recorder(ok({"message": {"items": []}}))
    run(CrossrefDiscovery("", SETTINGS, **http_kwargs(handler)).search("x"))
    assert "mailto" not in handler.requests[0].url.params

def test_crossref_lookup_doi_verifies():
    handler = Recorder(ok({"status": "ok", "message": CROSSREF_WORK}))
    r = run(CrossrefDiscovery("", SETTINGS, **http_kwargs(handler))
            .lookup_doi("https://doi.org/10.1016/J.WATRES.2019.01.001"))
    assert handler.requests[0].url.path == "/works/10.1016/j.watres.2019.01.001"
    assert r.doi_verified is True and r.rank is None and r.provider_ids["crossref"] == r.doi

def test_crossref_unknown_doi_is_none_not_error():
    provider = CrossrefDiscovery("", SETTINGS, **http_kwargs(Recorder(ok({}, 404))))
    assert run(provider.lookup_doi("10.9999/nope")) is None
    assert run(provider.lookup_doi("not a doi")) is None

def test_crossref_lookup_malformed():
    provider = CrossrefDiscovery("", SETTINGS, **http_kwargs(Recorder(ok({"status": "ok"}))))
    with pytest.raises(ProviderError) as err:
        run(provider.lookup_doi("10.1234/x"))
    assert err.value.kind is ProviderErrorKind.MALFORMED

def test_crossref_handles_partial_dates_and_missing_fields():
    item = {"DOI": "10.1234/x", "title": ["T"], "issued": {"date-parts": [[2020]]}, "author": "bad"}
    handler = Recorder(ok({"message": {"items": [item, {"title": []}]}}))
    [r] = run(CrossrefDiscovery("", SETTINGS, **http_kwargs(handler)).search("x"))
    assert r.published_date == "2020" and r.url == "https://doi.org/10.1234/x" and r.authors == []

# --- SearXNG --------------------------------------------------------------------------

def test_searxng_maps_results_and_classifies_sources():
    handler = Recorder(ok(SEARXNG_SEARCH))
    results = run(SearXNGDiscovery("http://searx:8080/", SETTINGS, **http_kwargs(handler))
                  .search("toronto water", 10))
    req = handler.requests[0]
    assert str(req.url).startswith("http://searx:8080/search?")
    assert req.url.params["format"] == "json"
    assert [r.title for r in results] == ["Toronto Water", "Drinking water"]
    assert [r.source_type for r in results] == ["UTILITY", "GOVERNMENT"]
    assert results[1].published_date == "2023-05-02"

def test_searxng_respects_limit():
    handler = Recorder(ok(SEARXNG_SEARCH))
    assert len(run(SearXNGDiscovery("http://s", SETTINGS, **http_kwargs(handler))
                   .search("x", 1))) == 1

def test_searxng_json_disabled_gives_actionable_error():
    provider = SearXNGDiscovery("http://s", SETTINGS, **http_kwargs(Recorder(ok({}, 403))))
    with pytest.raises(ProviderError) as err:
        run(provider.search("x"))
    assert "search.formats" in str(err.value)

def test_searxng_unconfigured():
    with pytest.raises(ProviderError) as err:
        run(SearXNGDiscovery("", SETTINGS).search("x"))
    assert err.value.kind is ProviderErrorKind.UNCONFIGURED

def test_searxng_timeout():
    provider = SearXNGDiscovery("http://s", SETTINGS, **http_kwargs(Recorder(timeout)))
    with pytest.raises(ProviderError) as err:
        run(provider.search("x"))
    assert err.value.kind is ProviderErrorKind.TIMEOUT

def test_publications_module_still_exports_adapters():
    from water_source_research.discovery import publications
    assert publications.OpenAlexDiscovery is OpenAlexDiscovery
