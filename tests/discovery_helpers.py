"""Offline fixtures for discovery tests: mocked HTTP transports, fake providers, payloads."""

from collections.abc import Callable
import httpx
from water_source_research.discovery.base import SearchResult
from water_source_research.discovery.config import ALL_PROVIDERS, DiscoverySettings

# No throttling by default so recorded sleeps are backoff only (throttle has its own test).
SETTINGS = DiscoverySettings(max_retries=2, backoff_base_seconds=1, backoff_max_seconds=5,
                             crossref_verify_top_n=5)
for _p in ALL_PROVIDERS:
    SETTINGS.limits_for(_p).min_interval_seconds = 0.0

class Sleeps:
    """Injected ``sleep``: records requested delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)

class Recorder:
    """A MockTransport handler that records requests and replays scripted responses."""

    def __init__(self, *responses: httpx.Response | Exception | Callable) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(item) and not isinstance(item, httpx.Response):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        return item

def http_kwargs(handler: Recorder, sleeps: Sleeps | None = None, **extra) -> dict:
    return {"transport": httpx.MockTransport(handler), "sleep": sleeps or Sleeps(),
            "jitter": lambda: 1.0, **extra}

def ok(data, status: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=data, headers=headers)

def timeout(request: httpx.Request) -> Exception:
    return httpx.ReadTimeout("timed out", request=request)

class FakeProvider:
    def __init__(self, name: str, results=None, error: Exception | None = None,
                 per_query: dict[str, list[SearchResult] | Exception] | None = None) -> None:
        self.name = name
        self.results = results or []
        self.error = error
        self.per_query = per_query or {}
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        self.queries.append(query)
        outcome = self.per_query.get(query, self.error or self.results)
        if isinstance(outcome, Exception):
            raise outcome
        return [SearchResult.from_dict({**r.to_dict(), "query": query}) for r in outcome][:limit]

class FakeCrossref:
    name = "crossref"

    def __init__(self, records: dict[str, SearchResult | None | Exception]) -> None:
        self.records = records
        self.lookups: list[str] = []

    async def lookup_doi(self, doi: str) -> SearchResult | None:
        self.lookups.append(doi)
        found = self.records.get(doi)
        if isinstance(found, Exception):
            raise found
        return found

def web(title: str, url: str, rank: int = 1, **kw) -> SearchResult:
    return SearchResult(title=title, url=url, provider="searxng", rank=rank, **kw)

def paper(title: str, doi: str, provider: str = "openalex", rank: int = 1, **kw) -> SearchResult:
    kw.setdefault("url", f"https://doi.org/{doi}")
    return SearchResult(title=title, doi=doi, provider=provider, rank=rank,
                        source_type="ACADEMIC", **kw)

# --- realistic (trimmed) provider payloads ---------------------------------------------

OPENALEX_WORKS = {"meta": {"count": 1}, "results": [{
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1016/J.WATRES.2019.01.001",
    "display_name": "Drinking water sources of Toronto",
    "publication_date": "2019-03-01",
    "primary_location": {"landing_page_url": "https://www.sciencedirect.com/science/article/1",
                         "source": {"display_name": "Water Research"}},
    "open_access": {"oa_url": None},
    "authorships": [{"author": {"display_name": "A. Smith"}},
                    {"author": {"display_name": "B. Jones"}}],
    "abstract_inverted_index": {"Toronto": [0], "draws": [1], "from": [2], "Lake": [3],
                                "Ontario": [4]},
}]}

S2_SEARCH = {"total": 1, "offset": 0, "data": [{
    "paperId": "649def34f8be52c8b66281af98ae884c09aef38b",
    "title": "Drinking Water Sources of Toronto",
    "url": "https://www.semanticscholar.org/paper/649def34",
    "abstract": "Toronto draws from Lake Ontario.",
    "externalIds": {"DOI": "10.1016/j.watres.2019.01.001", "CorpusId": 1},
    "publicationDate": None, "year": 2019, "venue": "Water Research",
    "authors": [{"authorId": "1", "name": "A. Smith"}],
    "openAccessPdf": None,
}]}

CROSSREF_WORK = {
    "DOI": "10.1016/j.watres.2019.01.001",
    "title": ["Drinking water sources of Toronto"],
    "URL": "https://doi.org/10.1016/j.watres.2019.01.001",
    "published": {"date-parts": [[2019, 2, 14]]},
    "container-title": ["Water Research"],
    "author": [{"given": "Alice", "family": "Smith"}],
    "abstract": "<jats:p>Toronto draws from <jats:italic>Lake Ontario</jats:italic>.</jats:p>",
}

SEARXNG_SEARCH = {"query": "toronto water", "results": [
    {"url": "https://www.toronto.ca/services-payments/water-environment/", "title": "Toronto Water",
     "content": "Toronto Water supplies drinking water.", "engine": "duckduckgo",
     "publishedDate": None},
    {"url": "https://www.canada.ca/en/health-canada/water.html", "title": "Drinking water",
     "content": "Government of Canada", "publishedDate": "2023-05-02T00:00:00"},
    {"title": "no url"},
]}
