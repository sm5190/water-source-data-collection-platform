"""SearXNG (self-hosted metasearch) for general web discovery: utility sites, government
documents, technical reports. Needs the JSON output format enabled in SearXNG's settings.yml.
"""

from typing import Any
from water_source_research.core.config import get_settings
from .base import SearchResult
from .config import DiscoverySettings
from .http import ProviderError, ProviderErrorKind, ProviderHttpClient, expect_items, text
from .normalize import classify_source_type

class SearXNGDiscovery:
    name = "searxng"

    def __init__(self, base_url: str | None = None, settings: DiscoverySettings | None = None,
                 **http_kwargs: Any) -> None:
        app = get_settings()
        self.base_url = (app.searxng_url if base_url is None else base_url).strip().rstrip("/")
        self.http = ProviderHttpClient(self.name, self.base_url, settings or app.discovery,
                                       **http_kwargs)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not self.base_url:
            raise ProviderError(self.name, ProviderErrorKind.UNCONFIGURED, "SEARXNG_URL not set")
        try:
            payload = await self.http.get_json("/search", {
                "q": query, "format": "json", "categories": "general", "safesearch": 0,
            })
        except ProviderError as exc:
            if exc.status == 403:
                raise ProviderError(self.name, ProviderErrorKind.HTTP,
                                    "JSON API disabled: add 'json' to search.formats in "
                                    "SearXNG settings.yml", 403) from None
            raise
        results: list[SearchResult] = []
        for item in expect_items(self.name, payload, "results", missing_ok=True):
            url, title = text(item.get("url")), text(item.get("title"))
            if not url:
                continue
            title = title or url
            results.append(SearchResult(
                title=title, url=url, snippet=text(item.get("content")), provider=self.name,
                rank=len(results) + 1, published_date=_date(item.get("publishedDate")),
                source_type=classify_source_type(url, title, self.name), query=query,
            ))
            if len(results) >= limit:
                break
        return results

def _date(value: Any) -> str | None:
    value = text(value)
    return value[:10] if value and value[:4].isdigit() else None
