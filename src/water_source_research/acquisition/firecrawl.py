import httpx
from water_source_research.core.config import get_settings
from .base import AcquiredDocument

class FirecrawlAcquirer:
    def __init__(self) -> None:
        self.api_key = get_settings().firecrawl_api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def acquire(self, url: str) -> AcquiredDocument:
        if not self.configured:
            raise RuntimeError("Firecrawl is not configured")
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"url": url, "formats": ["markdown"]},
            )
            r.raise_for_status()
        body = r.json().get("data", r.json())
        return AcquiredDocument(
            url=url,
            title=(body.get("metadata") or {}).get("title"),
            media_type="text/markdown",
            text=body.get("markdown", ""),
            acquisition_method="FIRECRAWL",
        )
