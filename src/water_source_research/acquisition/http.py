import httpx
import trafilatura
from bs4 import BeautifulSoup
from .base import AcquiredDocument

class DirectHTTPAcquirer:
    async def acquire(self, url: str) -> AcquiredDocument:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "VT-Water-Source-Research/0.1"})
            r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise ValueError(f"Expected HTML, got {content_type}")
        extracted = trafilatura.extract(r.text, include_tables=True, include_links=True)
        if not extracted:
            extracted = BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True)
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else None
        return AcquiredDocument(
            url=str(r.url),
            title=title,
            media_type=content_type or "text/html",
            text=extracted,
            acquisition_method="DIRECT_HTTP",
        )
