import httpx
from water_source_research.core.config import get_settings
from .base import SearchResult

class SearXNGDiscovery:
    def __init__(self) -> None:
        self.base_url = get_settings().searxng_url.rstrip("/")

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.base_url}/search", params={"q": query, "format": "json"})
            r.raise_for_status()
        return [
            SearchResult(
                title=x.get("title", ""),
                url=x.get("url", ""),
                snippet=x.get("content"),
                provider="searxng",
                rank=i,
            )
            for i, x in enumerate(r.json().get("results", [])[:limit], 1)
        ]
