"""Crossref REST API: DOI resolution and citation-metadata verification.

No key. ``CROSSREF_MAILTO`` (a contact email, sent as ``mailto`` and in the User-Agent)
routes requests to Crossref's "polite" pool with better rate limits.
"""

import re
from typing import Any
from urllib.parse import quote
from water_source_research.core.config import get_settings
from .base import SearchResult
from .config import DiscoverySettings
from .http import (
    USER_AGENT, ProviderError, ProviderErrorKind, ProviderHttpClient, expect_items, text,
)
from .normalize import iso_date, normalize_doi

BASE_URL = "https://api.crossref.org"
SELECT = "DOI,title,URL,issued,published,container-title,author,abstract"
MAX_AUTHORS = 10
MAX_SNIPPET_CHARS = 500
_TAGS = re.compile(r"<[^>]+>")

class CrossrefDiscovery:
    name = "crossref"

    def __init__(self, mailto: str | None = None, settings: DiscoverySettings | None = None,
                 *, base_url: str = BASE_URL, **http_kwargs: Any) -> None:
        app = get_settings()
        mailto = (app.crossref_mailto if mailto is None else mailto).strip()
        headers = {"User-Agent": f"{USER_AGENT} (mailto:{mailto})"} if mailto else {}
        self.http = ProviderHttpClient(self.name, base_url, settings or app.discovery,
                                       headers=headers, default_params={"mailto": mailto},
                                       **http_kwargs)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        payload = await self.http.get_json("/works", {
            "query.bibliographic": query, "rows": min(limit, 100), "select": SELECT,
        })
        results: list[SearchResult] = []
        for item in expect_items(self.name, payload, "message", "items", missing_ok=True):
            result = to_result(item, query, len(results) + 1)
            if result:
                results.append(result)
            if len(results) >= limit:
                break
        return results

    async def lookup_doi(self, doi: str) -> SearchResult | None:
        """Authoritative metadata for a DOI; ``None`` when Crossref doesn't know it."""
        doi = normalize_doi(doi) or ""
        if not doi:
            return None
        payload = await self.http.get_json(f"/works/{quote(doi, safe='/')}", not_found_ok=True)
        if payload is None:
            return None
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise ProviderError(self.name, ProviderErrorKind.MALFORMED, "missing 'message'")
        result = to_result(message, f"doi:{doi}", 1)
        if result:
            result.doi_verified = True
            result.rank = None  # a lookup, not a search hit: no relevance signal
        return result

def to_result(item: dict, query: str, rank: int) -> SearchResult | None:
    doi = normalize_doi(text(item.get("DOI")))
    titles = item.get("title")
    title = text(titles[0]) if isinstance(titles, list) and titles else text(titles)
    url = text(item.get("URL")) or (f"https://doi.org/{doi}" if doi else None)
    if not url or not title:
        return None
    published = None
    for key in ("published", "issued", "published-print", "published-online"):
        value = item.get(key)
        if isinstance(value, dict) and (published := iso_date(value.get("date-parts"))):
            break
    venues = item.get("container-title")
    venue = text(venues[0]) if isinstance(venues, list) and venues else None
    authors = []
    for a in (item.get("author") or [])[:MAX_AUTHORS]:
        if isinstance(a, dict):
            name = " ".join(p for p in (text(a.get("given")), text(a.get("family"))) if p) \
                or text(a.get("name"))
            if name:
                authors.append(name)
    abstract = text(item.get("abstract"))
    snippet = " ".join(_TAGS.sub(" ", abstract).split())[:MAX_SNIPPET_CHARS] if abstract \
        else None
    return SearchResult(
        title=title, url=url, snippet=snippet, provider="crossref", rank=rank, doi=doi,
        published_date=published, authors=authors, venue=venue, source_type="ACADEMIC",
        provider_ids={"crossref": doi} if doi else {}, query=query,
    )
