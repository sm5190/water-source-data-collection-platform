import asyncio
from discovery_helpers import (
    OPENALEX_WORKS, SEARXNG_SEARCH, SETTINGS, FakeCrossref, FakeProvider, Recorder, http_kwargs,
    ok, paper, timeout, web,
)
from water_source_research.core.config import Settings
from water_source_research.discovery.http import ProviderError, ProviderErrorKind
from water_source_research.discovery.queries import PlannedQuery
from water_source_research.discovery.service import DiscoveryService, build_discovery_service

DOI = "10.1016/j.watres.2019.01.001"
WEB_Q = PlannedQuery("web", "Toronto Canada drinking water utility", "utility", "utility_name")
ACAD_Q = PlannedQuery("academic", "Toronto urban water supply", "utility", "utility_name")

def discover(service, queries=(WEB_Q, ACAD_Q)):
    return asyncio.run(service.discover(list(queries), city="Toronto", country="Canada"))

def rate_limited(name):
    return ProviderError(name, ProviderErrorKind.RATE_LIMITED, "HTTP 429", 429)

def test_web_and_academic_queries_go_to_their_providers():
    searx = FakeProvider("searxng", [web("Toronto Water", "https://www.toronto.ca/water")])
    oa = FakeProvider("openalex", [paper("Toronto water supply history", DOI)])
    out = discover(DiscoveryService(SETTINGS, web=[searx], scholarly=[oa]))
    assert searx.queries == [WEB_Q.query] and oa.queries == [ACAD_Q.query]
    assert [c.source_type for c in out.candidates] == ["UTILITY", "ACADEMIC"]
    assert out.provider_status == {"searxng": "ok", "openalex": "ok"}
    assert {(c.provider, c.strategy, c.result, c.field_key) for c in out.calls} == {
        ("searxng", "web_search", "SUCCESS", "utility_name"),
        ("openalex", "scholarly_search", "SUCCESS", "utility_name")}

def test_one_failing_provider_does_not_fail_the_round():
    searx = FakeProvider("searxng", [web("Toronto Water", "https://www.toronto.ca/water")])
    oa = FakeProvider("openalex", error=ProviderError("openalex", ProviderErrorKind.TIMEOUT))
    s2 = FakeProvider("semantic_scholar", error=RuntimeError("boom"))
    out = discover(DiscoveryService(SETTINGS, web=[searx], scholarly=[oa, s2]))
    assert [c.url for c in out.candidates] == ["https://www.toronto.ca/water"]
    assert out.provider_status == {"searxng": "ok", "openalex": "timeout",
                                   "semantic_scholar": "error"}
    failed = {c.provider: c for c in out.calls if c.result == "FAILURE"}
    assert failed["openalex"].strategy == "timeout" and failed["semantic_scholar"].error == "error"

def test_all_providers_failing_returns_empty_outcome():
    providers = [FakeProvider(n, error=rate_limited(n)) for n in ("openalex", "semantic_scholar")]
    out = discover(DiscoveryService(SETTINGS, web=[FakeProvider("searxng", error=OSError())],
                                    scholarly=providers))
    assert out.candidates == []
    assert out.provider_status == {"searxng": "error", "openalex": "rate_limited",
                                   "semantic_scholar": "rate_limited"}

def test_partial_failure_is_reported_as_degraded():
    q2 = PlannedQuery("web", "Toronto water production annual report", "withdrawal",
                      "total_city_water_withdrawals_m3_day")
    searx = FakeProvider("searxng", [web("Toronto Water", "https://toronto.ca/w")],
                         per_query={q2.query: rate_limited("searxng")})
    out = discover(DiscoveryService(SETTINGS, web=[searx]), [WEB_Q, q2])
    assert out.provider_status == {"searxng": "degraded:rate_limited"}
    assert len(out.candidates) == 1

def test_empty_results_are_no_result_not_failure():
    out = discover(DiscoveryService(SETTINGS, web=[FakeProvider("searxng", [])]), [WEB_Q])
    assert out.provider_status == {"searxng": "no_results"}
    assert [c.result for c in out.calls] == ["NO_RESULT"]

def test_duplicate_publications_across_providers_are_merged():
    oa = FakeProvider("openalex", [paper("Toronto water sources", f"https://doi.org/{DOI}")])
    s2 = FakeProvider("semantic_scholar", [paper("Toronto Water Sources", DOI.upper(),
                                                 provider="semantic_scholar",
                                                 url="https://s2.example/p")])
    out = discover(DiscoveryService(SETTINGS, scholarly=[oa, s2]), [ACAD_Q])
    [c] = out.candidates
    assert c.providers == ["openalex", "semantic_scholar"]
    assert [call.sources_retained for call in out.calls] == [1, 1]

def test_crossref_verifies_and_enriches_top_dois():
    oa = FakeProvider("openalex", [paper("Toronto water sources", DOI),
                                   paper("Unknown DOI paper on Toronto", "10.9999/unknown")])
    enriched = paper("Toronto water sources", DOI, provider="crossref", rank=None,
                     published_date="2019-02-14", venue="Water Research", doi_verified=True)
    crossref = FakeCrossref({DOI: enriched, "10.9999/unknown": None})
    out = discover(DiscoveryService(SETTINGS, scholarly=[oa], crossref=crossref), [ACAD_Q])
    by_doi = {c.doi: c for c in out.candidates}
    assert by_doi[DOI].doi_verified is True and by_doi[DOI].published_date == "2019-02-14"
    assert "crossref" in by_doi[DOI].providers
    assert by_doi["10.9999/unknown"].doi_verified is False
    [verify] = [c for c in out.calls if c.provider == "crossref"]
    assert (verify.strategy, verify.result, verify.sources_found, verify.sources_retained,
            verify.query) == ("doi_verification", "SUCCESS", 2, 1, None)
    assert out.provider_status["crossref"] == "ok"

def test_crossref_failure_leaves_candidates_untouched():
    oa = FakeProvider("openalex", [paper("Toronto water sources", DOI)])
    crossref = FakeCrossref({DOI: rate_limited("crossref")})
    out = discover(DiscoveryService(SETTINGS, scholarly=[oa], crossref=crossref), [ACAD_Q])
    [c] = out.candidates
    assert c.doi_verified is None and c.providers == ["openalex"]
    assert out.provider_status["crossref"] == "rate_limited"

def test_crossref_skipped_without_dois():
    crossref = FakeCrossref({})
    out = discover(DiscoveryService(SETTINGS, web=[FakeProvider(
        "searxng", [web("Toronto Water", "https://toronto.ca/w")])], crossref=crossref), [WEB_Q])
    assert crossref.lookups == [] and "crossref" not in out.provider_status

def test_related_paper_expansion_when_enabled():
    settings = SETTINGS.model_copy(update={"semantic_scholar_related_per_round": 1})

    class S2(FakeProvider):
        async def related(self, paper_id, limit=10):
            return [paper("Related Toronto water paper", "10.1234/rel",
                          provider="semantic_scholar", rank=1)]

    s2 = S2("semantic_scholar", [paper("Toronto water sources", DOI, provider="semantic_scholar",
                                       provider_ids={"semantic_scholar": "abc"})])
    svc = DiscoveryService(settings, scholarly=[s2])
    svc.related = s2  # the fake isn't a SemanticScholarDiscovery instance
    out = discover(svc, [ACAD_Q])
    assert {c.doi for c in out.candidates} == {DOI, "10.1234/rel"}
    assert any(c.strategy == "related_papers" for c in out.calls)

def test_end_to_end_with_mocked_http_including_timeout():
    """Real adapters over MockTransport: SearXNG ok, OpenAlex ok, Semantic Scholar timing out."""
    settings = SETTINGS.model_copy(update={"providers": ["searxng", "openalex",
                                                        "semantic_scholar"]})
    app = Settings(searxng_url="http://searx:8080", openalex_api_key="k",
                   semantic_scholar_api_key="", crossref_mailto="", discovery=settings)

    def route(request):
        host = request.url.host
        if host == "searx":
            return ok(SEARXNG_SEARCH)
        if host == "api.openalex.org":
            return ok(OPENALEX_WORKS)
        return timeout(request)

    svc = build_discovery_service(app, **http_kwargs(Recorder(route)))
    out = discover(svc)
    assert out.provider_status == {"searxng": "ok", "openalex": "ok",
                                   "semantic_scholar": "timeout"}
    urls = [c.url for c in out.candidates]
    # Toronto's own utility page first; the Toronto paper outranks a national page that
    # doesn't mention the city.
    assert urls == ["https://www.toronto.ca/services-payments/water-environment/",
                    "https://www.sciencedirect.com/science/article/1",
                    "https://www.canada.ca/en/health-canada/water.html"]
    assert out.candidates[1].doi == DOI

def test_build_discovery_service_from_settings():
    assert build_discovery_service(Settings(discovery={"enabled": False})) is None
    svc = build_discovery_service(Settings(searxng_url="", discovery={"providers": [
        "searxng", "openalex"]}))
    assert svc.provider_names == ["openalex"]  # no SearXNG URL -> no web provider
    full = build_discovery_service(Settings(searxng_url="http://s"))
    assert full.provider_names == ["searxng", "openalex", "semantic_scholar", "crossref"]

def test_env_overrides_keep_other_provider_defaults(monkeypatch):
    monkeypatch.setenv("DISCOVERY__SEMANTIC_SCHOLAR_LIMITS__MIN_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("DISCOVERY__PROVIDERS", '["openalex","crossref"]')
    d = Settings(_env_file=None).discovery
    assert d.providers == ["openalex", "crossref"]
    assert (d.semantic_scholar_limits.max_concurrency,
            d.semantic_scholar_limits.min_interval_seconds) == (1, 2.5)
