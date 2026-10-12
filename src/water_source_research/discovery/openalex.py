"""OpenAlex works search (academic discovery). API key from ``OPENALEX_API_KEY``."""

import logging
from typing import Any
from water_source_research.core.config import get_settings
from .base import SearchResult
from .config import DiscoverySettings
from .http import ProviderHttpClient, expect_items, text
from .normalize import normalize_doi

log = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"
SELECT = ("id,doi,display_name,publication_date,primary_location,open_access,authorships,"
          "abstract_inverted_index")
MAX_SNIPPET_WORDS = 60
MAX_AUTHORS = 10

class OpenAlexDiscovery:
    name = "openalex"

    def __init__(self, api_key: str | None = None, settings: DiscoverySettings | None = None,
                 *, base_url: str = BASE_URL, **http_kwargs: Any) -> None:
        app = get_settings()
        api_key = app.openalex_api_key if api_key is None else api_key
        if not api_key:
            log.warning("OPENALEX_API_KEY not set; OpenAlex runs keyless with lower limits")
        self.http = ProviderHttpClient(
            self.name, base_url, settings or app.discovery,
            default_params={"api_key": api_key}, secret_params=frozenset({"api_key"}),
            **http_kwargs)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        payload = await self.http.get_json("/works", {
            "search": query, "per-page": min(limit, 200), "select": SELECT,
        })
        results: list[SearchResult] = []
        for work in expect_items(self.name, payload, "results", missing_ok=True):
            result = _to_result(work, query, len(results) + 1)
            if result:
                results.append(result)
            if len(results) >= limit:
                break
        return results

def _to_result(work: dict, query: str, rank: int) -> SearchResult | None:
    doi = normalize_doi(text(work.get("doi")))
    location = work.get("primary_location") if isinstance(work.get("primary_location"),
                                                          dict) else {}
    source = location.get("source") if isinstance(location.get("source"), dict) else {}
    open_access = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    work_id = text(work.get("id"))
    url = (text(location.get("landing_page_url")) or text(open_access.get("oa_url"))
           or (f"https://doi.org/{doi}" if doi else None) or work_id)
    title = text(work.get("display_name")) or text(work.get("title"))
    if not url or not title:
        return None
    authors = [name for a in (work.get("authorships") or [])[:MAX_AUTHORS]
               if isinstance(a, dict) and isinstance(a.get("author"), dict)
               and (name := text(a["author"].get("display_name")))]
    return SearchResult(
        title=title, url=url, snippet=_abstract(work.get("abstract_inverted_index")),
        provider="openalex", rank=rank, doi=doi,
        published_date=text(work.get("publication_date")), authors=authors,
        venue=text(source.get("display_name")), source_type="ACADEMIC",
        provider_ids={"openalex": work_id.rsplit("/", 1)[-1]} if work_id else {},
        query=query,
    )

def _abstract(index: Any) -> str | None:
    """Rebuild the first words of an abstract from OpenAlex's inverted index."""
    if not isinstance(index, dict):
        return None
    positions: dict[int, str] = {}
    for word, places in index.items():
        if isinstance(places, list):
            for p in places:
                if isinstance(p, int) and p < MAX_SNIPPET_WORDS:
                    positions[p] = word
    if not positions:
        return None
    return " ".join(positions[p] for p in sorted(positions))
