import hashlib
import trafilatura
from bs4 import BeautifulSoup
from .base import AcquiredDocument
from .config import AcquisitionSettings
from .safe_http import FetchError, FetchErrorKind, SafeFetcher

class DirectHTTPAcquirer:
    """HTML pages over plain HTTP (trafilatura, BeautifulSoup fallback).

    Fetching goes through ``SafeFetcher`` (SSRF checks, robots.txt, size cap, rate limits).
    """

    def __init__(self, fetcher: SafeFetcher | None = None,
                 settings: AcquisitionSettings | None = None) -> None:
        self.settings = settings or AcquisitionSettings()
        self.fetcher = fetcher or SafeFetcher(self.settings)

    async def acquire(self, url: str) -> AcquiredDocument:
        r = await self.fetcher.fetch(url, self.settings.max_html_bytes)
        if "html" not in r.content_type.lower():
            raise FetchError(FetchErrorKind.UNSUPPORTED_TYPE,
                             f"Expected HTML, got {r.content_type}")
        doc = parse_html(r.final_url, r.body, r.content_type)
        doc.retrieved_at = r.fetched_at
        return doc

def parse_html(url: str, body: bytes, content_type: str = "text/html") -> AcquiredDocument:
    html = _decode(body, content_type)
    extracted = trafilatura.extract(html, include_tables=True, include_links=False)
    soup = BeautifulSoup(html, "html.parser")
    if not extracted:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    if not extracted:
        extracted = (soup.body or soup).get_text("\n", strip=True)
        if extracted == title:  # only the <title>: no body text
            extracted = ""
    source_date = None
    try:
        meta = trafilatura.extract_metadata(html)
        source_date = getattr(meta, "date", None) if meta else None
    except Exception:  # noqa: BLE001 - metadata is optional
        source_date = None
    return AcquiredDocument(
        url=url,
        title=title or None,
        media_type=content_type or "text/html",
        text=extracted or "",
        acquisition_method="DIRECT_HTTP",
        source_date=source_date,
        content_sha256=hashlib.sha256(body).hexdigest(),
    )

def _decode(body: bytes, content_type: str) -> str:
    charset = None
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip('"\' ')
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
