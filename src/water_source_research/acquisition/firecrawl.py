"""Firecrawl v2 scrape client: a fetch fallback for pages the direct fetcher read as empty or
couldn't reach (acquisition only; SearXNG stays the search).

Request policy (docs checked 2026-11-03): fresh retrieval (``maxAge: 0``), nothing kept in
Firecrawl's cache (``storeInCache: false``), the full page (``onlyMainContent: false``: its
main-content filter is not documented beyond "nav, footer, etc."), basic proxy only (never
``auto``/``enhanced``: no workaround for access restrictions), markdown + rendered HTML, PDFs
bounded by ``maxPages`` with per-page output (``pages: true``) and page markers as fallback.

The scrape response reports no credits, so callers estimate them (1 per returned document,
+1 per parsed PDF page) and never count an unknown charge as zero. The API key goes only in
the Authorization header; it is never logged, traced or stored.
"""

import asyncio
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlsplit
import httpx

SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
CREDIT_USAGE_URL = "https://api.firecrawl.dev/v2/team/credit-usage"

class FirecrawlErrorKind(StrEnum):
    AUTH = "AUTH"  # 401: bad key -> Firecrawl off for the run
    CREDITS_EXHAUSTED = "CREDITS_EXHAUSTED"  # 402 -> off for the run
    RATE_LIMIT = "RATE_LIMIT"  # 429 after one retry
    TIMEOUT = "TIMEOUT"  # client timeout / 408: outcome UNCERTAIN (may have been billed)
    NETWORK = "NETWORK"  # connection dropped: UNCERTAIN
    SERVER = "SERVER"  # 5xx: UNCERTAIN unless the body says it failed
    BAD_RESPONSE = "BAD_RESPONSE"  # 2xx without a usable body: UNCERTAIN
    REQUEST = "REQUEST"  # other 4xx (request rejected before any work): not billed

# Outcomes where Firecrawl may have done (and billed) the work although we got no answer.
UNCERTAIN_KINDS = frozenset({FirecrawlErrorKind.TIMEOUT, FirecrawlErrorKind.NETWORK,
                             FirecrawlErrorKind.SERVER, FirecrawlErrorKind.BAD_RESPONSE})

class FirecrawlError(Exception):
    def __init__(self, kind: FirecrawlErrorKind, message: str, status: int | None = None):
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.message = message
        self.status = status

    @property
    def uncertain(self) -> bool:
        return self.kind in UNCERTAIN_KINDS

@dataclass(slots=True)
class FirecrawlResult:
    markdown: str
    raw_html: str
    metadata: dict[str, Any]
    pages: list[tuple[int, str]] | None  # Firecrawl's own per-page output (1-based)
    warning: str | None = None
    attempts: int = 1
    fields_seen: list[str] = field(default_factory=list)  # response keys, for the pilot

    @property
    def final_url(self) -> str | None:
        return self.metadata.get("url") or self.metadata.get("sourceURL")

    @property
    def status_code(self) -> int | None:
        code = self.metadata.get("statusCode")
        return int(code) if isinstance(code, int | float) else None

# --- request ---------------------------------------------------------------------------------

def scrape_body(url: str, *, pdf_max_pages: int, timeout_ms: int = 60_000) -> dict[str, Any]:
    return {"url": url, "formats": ["markdown", "rawHtml"], "onlyMainContent": False,
            "maxAge": 0, "storeInCache": False, "proxy": "basic", "blockAds": True,
            "timeout": timeout_ms,
            "parsers": [{"type": "pdf", "mode": "auto", "maxPages": max(1, pdf_max_pages),
                         "pages": True, "pageMarkers": True}]}

# Query parameters that look like credentials or one-time grants: such a URL is never sent.
_SENSITIVE = re.compile(r"^(?:.*token.*|.*secret.*|.*password.*|passwd|pwd|api[-_]?key|key|"
                        r"apikey|access[-_]?key|sig|signature|session|sessionid|sid|auth|"
                        r"authorization|code|otp|x-amz-.*|x-goog-.*|awsaccesskeyid|"
                        r"credential|jwt|ticket)$", re.IGNORECASE)

def sensitive_url(url: str) -> str | None:
    """Why ``url`` must not be sent to a third party, else None."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        return "credentials in URL"
    for name, _ in parse_qsl(parts.query, keep_blank_values=True):
        if _SENSITIVE.match(name):
            return f"sensitive query parameter {name!r}"
    if _SENSITIVE.match(parts.fragment.split("=", 1)[0] or "-"):
        return "sensitive fragment"
    return None

class FirecrawlClient:
    def __init__(self, api_key: str, *, timeout_seconds: float = 90.0,
                 client: httpx.AsyncClient | None = None,
                 sleep=asyncio.sleep) -> None:
        if not api_key:
            raise ValueError("Firecrawl API key missing")
        self._key = api_key
        self._timeout = timeout_seconds
        self._client = client
        self._sleep = sleep

    def __repr__(self) -> str:  # never show the key
        return "FirecrawlClient(<key hidden>)"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def scrape(self, url: str, *, pdf_max_pages: int) -> FirecrawlResult:
        body = scrape_body(url, pdf_max_pages=pdf_max_pages)
        attempts = 0
        while True:
            attempts += 1
            try:
                r = await self._http().post(SCRAPE_URL, headers=self._headers(), json=body,
                                            timeout=self._timeout)
            except httpx.TimeoutException:
                raise FirecrawlError(FirecrawlErrorKind.TIMEOUT, "client timeout") from None
            except httpx.HTTPError as exc:
                raise FirecrawlError(FirecrawlErrorKind.NETWORK, type(exc).__name__) from None
            if r.status_code == 429 and attempts == 1:
                await self._sleep(_retry_after(r))
                continue
            break
        payload = _json(r)
        if r.status_code >= 400:
            raise _http_error(r.status_code, payload)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or payload.get("success") is False:
            raise FirecrawlError(FirecrawlErrorKind.BAD_RESPONSE, "no data in response",
                                 r.status_code)
        return FirecrawlResult(
            markdown=data.get("markdown") or "", raw_html=data.get("rawHtml") or "",
            metadata=data.get("metadata") or {}, pages=_pages(data.get("pages")),
            warning=data.get("warning"), attempts=attempts,
            fields_seen=sorted(data) + [f"metadata.{k}" for k in sorted(
                data.get("metadata") or {})])

    async def remaining_credits(self) -> float | None:
        """The team's remaining credits (team-wide, approximate), or None."""
        try:
            r = await self._http().get(CREDIT_USAGE_URL, headers=self._headers(),
                                       timeout=30)
            value = (_json(r).get("data") or {}).get("remainingCredits")
            return float(value) if r.status_code == 200 and value is not None else None
        except (httpx.HTTPError, AttributeError, ValueError, TypeError):
            return None

def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        value = r.json()
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}

def _retry_after(r: httpx.Response) -> float:
    try:
        return min(30.0, max(1.0, float(r.headers.get("retry-after", "5"))))
    except ValueError:
        return 5.0

def _http_error(status: int, payload: dict[str, Any]) -> FirecrawlError:
    message = str(payload.get("error") or payload.get("code") or f"HTTP {status}")[:300]
    if status == 401:
        return FirecrawlError(FirecrawlErrorKind.AUTH, message, status)
    if status == 402:
        return FirecrawlError(FirecrawlErrorKind.CREDITS_EXHAUSTED, message, status)
    if status == 429:
        return FirecrawlError(FirecrawlErrorKind.RATE_LIMIT, message, status)
    if status == 408:
        return FirecrawlError(FirecrawlErrorKind.TIMEOUT, message, status)
    if status >= 500:
        return FirecrawlError(FirecrawlErrorKind.SERVER, message, status)
    return FirecrawlError(FirecrawlErrorKind.REQUEST, message, status)

def _pages(value: Any) -> list[tuple[int, str]] | None:
    """Firecrawl's per-page output [{pageNumber, markdown}] as (page, text); None when absent
    or when any entry lacks a page number (never guessed)."""
    if not isinstance(value, list) or not value:
        return None
    out = []
    for p in value:
        if not isinstance(p, dict) or not isinstance(p.get("pageNumber"), int):
            return None
        out.append((p["pageNumber"], p.get("markdown") or ""))
    return out

# --- PDFs: pages and truncation ----------------------------------------------------------

_MARKER = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)

def pdf_pages(result: FirecrawlResult) -> tuple[list[tuple[int, str]] | None, str]:
    """(pages, basis): Firecrawl's per-page output first, then ``<!-- page N -->`` markers,
    else (None, "none") = one text without page numbers."""
    if result.pages:
        return [(n, t) for n, t in result.pages if t.strip()], "pages"
    marks = list(_MARKER.finditer(result.markdown))
    if not marks:
        return None, "none"
    pages: list[tuple[int, str]] = []
    head = result.markdown[:marks[0].start()].strip()
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(result.markdown)
        text = result.markdown[m.end():end].strip()
        if i == 0 and head:  # text before the first marker: page N's text is after it
            text = (head + "\n\n" + text).strip()
        if text:
            pages.append((int(m.group(1)), text))
    return pages or None, "markers"

def coverage(result: FirecrawlResult, max_pages: int, parsed: int | None) -> dict[str, Any]:
    """{coverage FULL|PARTIAL, pages_parsed, pages_total, truncated} for a PDF."""
    total = result.metadata.get("totalPages")
    num = result.metadata.get("numPages")
    total = int(total) if isinstance(total, int | float) else None
    num = int(num) if isinstance(num, int | float) else parsed
    truncated = bool((total is not None and num is not None and total > num)
                     or (total is None and num is not None and num >= max_pages))
    return {"coverage": "PARTIAL" if truncated else "FULL", "pages_parsed": num,
            "pages_total": total, "truncated": truncated}

# --- choosing the evidence text ------------------------------------------------------------

_LINK_ONLY = re.compile(r"^\s*(?:[-*+]\s*)?(?:\[[^\]]*\]\([^)]*\)\s*[|·•/-]?\s*)+$")
_SENTENCE = re.compile(r"[^.!?\n]{20,}[.!?](?:\s|$)")
_HEADING = re.compile(r"^\s*#{1,6}\s+\S", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_DIGITS = re.compile(r"\d[\d.,]*")

def text_profile(text: str) -> dict[str, int]:
    from water_source_research.context_manager.topic import WATER
    lines = [ln for ln in (text or "").splitlines()
             if ln.strip() and not _LINK_ONLY.match(ln) and not ln.lstrip().startswith("![")]
    body = "\n".join(lines)
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    return {"body_chars": len(plain.strip()), "sentences": len(_SENTENCE.findall(plain)),
            "headings": len(_HEADING.findall(body)),
            "table_rows": len(_TABLE_ROW.findall(body)),
            "digits": len(_DIGITS.findall(plain)),
            "water_terms": len(re.findall(rf"\b(?:{WATER})\b", plain, re.IGNORECASE))}

def useful(p: dict[str, int]) -> bool:
    return p["body_chars"] >= 200 and p["sentences"] >= 3

def select_representation(markdown: str, html_text: str) -> tuple[str | None, dict[str, Any]]:
    """("MARKDOWN" | "RAW_HTML_EXTRACT" | None, reason with both profiles). Deterministic:
    the only useful text; else more water terms + table rows, then more body text, then
    markdown. Neither useful -> None (EMPTY). A rendered page's HTML may be a shell while the
    markdown holds the content, so neither is preferred by default."""
    pm, ph = text_profile(markdown), text_profile(html_text)
    reason: dict[str, Any] = {"markdown": pm, "raw_html_extract": ph}
    um, uh = useful(pm), useful(ph)
    if not um and not uh:
        reason["rule"] = "neither representation has enough body text"
        return None, reason
    if um != uh:
        reason["rule"] = "only one representation has enough body text"
        return ("MARKDOWN" if um else "RAW_HTML_EXTRACT"), reason
    km = (pm["water_terms"] + pm["table_rows"], pm["body_chars"])
    kh = (ph["water_terms"] + ph["table_rows"], ph["body_chars"])
    reason["rule"] = "both useful: more water terms and table rows, then more body text"
    return ("RAW_HTML_EXTRACT" if kh > km else "MARKDOWN"), reason
