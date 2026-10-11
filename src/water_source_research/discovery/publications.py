import httpx
from .base import SearchResult

class OpenAlexDiscovery:
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://api.openalex.org/works", params={"search": query, "per-page": limit})
            r.raise_for_status()
        out = []
        for i, work in enumerate(r.json().get("results", []), 1):
            loc = work.get("primary_location") or {}
            out.append(SearchResult(
                title=work.get("title", ""),
                url=loc.get("landing_page_url") or work.get("doi") or "",
                provider="openalex",
                rank=i,
            ))
        return out

class CrossrefDiscovery:
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://api.crossref.org/works", params={"query.bibliographic": query, "rows": limit})
            r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        return [
            SearchResult(
                title=(x.get("title") or [""])[0],
                url=x.get("URL", ""),
                provider="crossref",
                rank=i,
            )
            for i, x in enumerate(items, 1)
        ]

class SemanticScholarDiscovery:
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": query, "limit": limit, "fields": "title,url,abstract"},
            )
            r.raise_for_status()
        return [
            SearchResult(
                title=x.get("title", ""),
                url=x.get("url", ""),
                snippet=x.get("abstract"),
                provider="semantic_scholar",
                rank=i,
            )
            for i, x in enumerate(r.json().get("data", []), 1)
        ]
