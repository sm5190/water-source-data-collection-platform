"""Semantic Scholar Academic Graph API: paper search and related-paper recommendations.

``SEMANTIC_SCHOLAR_API_KEY`` is optional (sent as the ``x-api-key`` header); without it
requests share the public, lower-rate pool.
"""

from typing import Any
from urllib.parse import quote
from water_source_research.core.config import get_settings
from .base import SearchResult
from .config import DiscoverySettings
from .http import ProviderHttpClient, expect_items, text
from .normalize import normalize_doi

BASE_URL = "https://api.semanticscholar.org"
FIELDS = "title,url,abstract,externalIds,publicationDate,year,venue,authors,openAccessPdf"
MAX_AUTHORS = 10
MAX_SNIPPET_CHARS = 500

class SemanticScholarDiscovery:
    name = "semantic_scholar"

    def __init__(self, api_key: str | None = None, settings: DiscoverySettings | None = None,
                 *, base_url: str = BASE_URL, **http_kwargs: Any) -> None:
        app = get_settings()
        api_key = app.semantic_scholar_api_key if api_key is None else api_key
        headers = {"x-api-key": api_key} if api_key else {}
        self.http = ProviderHttpClient(self.name, base_url, settings or app.discovery,
                                       headers=headers, **http_kwargs)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        payload = await self.http.get_json("/graph/v1/paper/search", {
            "query": query, "limit": min(limit, 100), "fields": FIELDS,
        })
        # The API omits "data" when there are no matches.
        return _results(expect_items(self.name, payload, "data", missing_ok=True), query, limit)

    async def related(self, paper_id: str, limit: int = 10) -> list[SearchResult]:
        """Papers recommended for ``paper_id`` (S2 paperId or e.g. ``DOI:10.x/y``)."""
        payload = await self.http.get_json(
            f"/recommendations/v1/papers/forpaper/{quote(paper_id, safe=':')}",
            {"limit": min(limit, 100), "fields": FIELDS}, not_found_ok=True)
        if payload is None:
            return []
        items = expect_items(self.name, payload, "recommendedPapers", missing_ok=True)
        return _results(items, f"related:{paper_id}", limit)

def _results(items: list[dict], query: str, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for paper in items:
        result = _to_result(paper, query, len(results) + 1)
        if result:
            results.append(result)
        if len(results) >= limit:
            break
    return results

def _to_result(paper: dict, query: str, rank: int) -> SearchResult | None:
    ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    doi = normalize_doi(text(ids.get("DOI")))
    pdf = paper.get("openAccessPdf") if isinstance(paper.get("openAccessPdf"), dict) else {}
    paper_id = text(paper.get("paperId"))
    url = (text(paper.get("url")) or (f"https://doi.org/{doi}" if doi else None)
           or text(pdf.get("url")))
    title = text(paper.get("title"))
    if not url or not title:
        return None
    year = paper.get("year")
    published = text(paper.get("publicationDate")) or (str(year) if isinstance(year, int)
                                                        else None)
    abstract = text(paper.get("abstract"))
    authors = [name for a in (paper.get("authors") or [])[:MAX_AUTHORS]
               if isinstance(a, dict) and (name := text(a.get("name")))]
    return SearchResult(
        title=title, url=url, snippet=abstract[:MAX_SNIPPET_CHARS] if abstract else None,
        provider="semantic_scholar", rank=rank, doi=doi, published_date=published,
        authors=authors, venue=text(paper.get("venue")), source_type="ACADEMIC",
        provider_ids={"semantic_scholar": paper_id} if paper_id else {}, query=query,
    )
