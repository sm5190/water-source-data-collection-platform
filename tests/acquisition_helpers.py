"""Offline fixtures for acquisition tests: a mocked website, a fake DNS resolver, and a
hand-built minimal PDF (no PDF-writing dependency). No test touches the network."""

from collections.abc import Callable
from typing import Any
import httpx
from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.safe_http import SafeFetcher
from water_source_research.acquisition.service import AcquisitionService

PUBLIC_IP = "93.184.216.34"

def html_page(title: str, *paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<!doctype html><html><head><title>{title}</title></head><body><main>"
            f"<article><h1>{title}</h1>{body}</article></main></body></html>").encode()

def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

def make_pdf(pages: list[list[str]], title: str = "Test Report",
             mod_date: str = "D:20230415120000Z") -> bytes:
    """A valid multi-page PDF; each page is a list of text lines (Helvetica)."""
    objects: list[bytes] = []
    n_pages = len(pages)
    # 1 catalog, 2 pages, 3 font, 4 info, then (page, content) pairs.
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Title ({_pdf_escape(title)}) /ModDate ({mod_date}) >>".encode())
    for i, lines in enumerate(pages):
        content_id = 6 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode())
        ops = ["BT", "/F1 11 Tf", "14 TL", "50 750 Td"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                       + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)

Route = tuple[int, dict[str, str], bytes] | Callable[[httpx.Request], httpx.Response]

class Site:
    """MockTransport handler: url -> (status, headers, body). Unknown URLs are 404;
    robots.txt defaults to 404 (allow all) unless routed. Records every request."""

    def __init__(self, routes: dict[str, Route] | None = None) -> None:
        self.routes: dict[str, Route] = dict(routes or {})
        self.requests: list[str] = []

    def html(self, url: str, body: bytes) -> "Site":
        self.routes[url] = (200, {"content-type": "text/html; charset=utf-8"}, body)
        return self

    def pdf(self, url: str, body: bytes) -> "Site":
        self.routes[url] = (200, {"content-type": "application/pdf"}, body)
        return self

    def redirect(self, url: str, location: str, status: int = 302) -> "Site":
        self.routes[url] = (status, {"location": location}, b"")
        return self

    def status(self, url: str, code: int) -> "Site":
        self.routes[url] = (code, {"content-type": "text/plain"}, b"error")
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        route = self.routes.get(url)
        if route is None:
            return httpx.Response(404, content=b"not found")
        if callable(route):
            return route(request)
        code, headers, body = route
        return httpx.Response(code, headers=headers, content=body)

    def fetched(self, include_robots: bool = False) -> list[str]:
        return [u for u in self.requests if include_robots or not u.endswith("/robots.txt")]

class Resolver:
    """Fake DNS: host -> addresses (default: one public IP). Records lookups."""

    def __init__(self, mapping: dict[str, list[str]] | None = None) -> None:
        self.mapping = mapping or {}
        self.lookups: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        return self.mapping.get(host, [PUBLIC_IP])

async def _no_sleep(_: float) -> None:
    return None

def make_fetcher(site: Site, resolver: Resolver | None = None,
                 settings: AcquisitionSettings | None = None) -> SafeFetcher:
    settings = settings or AcquisitionSettings(per_host_min_interval_seconds=0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(site), follow_redirects=False)
    return SafeFetcher(settings, client=client, resolver=resolver or Resolver(),
                       sleep=_no_sleep)

def make_acquisition(site: Site, resolver: Resolver | None = None,
                     artifact_store: Any = None, **settings: Any) -> AcquisitionService:
    s = AcquisitionSettings(**{"per_host_min_interval_seconds": 0, **settings})
    return AcquisitionService(s, fetcher=make_fetcher(site, resolver, s),
                              artifact_store=artifact_store)

class MemoryArtifacts:
    """In-memory ArtifactStore; ``fail=True`` simulates an unreachable MinIO."""

    def __init__(self, fail: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = fail

    def put(self, data: bytes, sha256: str, content_type: str) -> str:
        if self.fail:
            raise ConnectionError("minio down")
        from water_source_research.acquisition.artifacts import object_key
        key = "test-bucket/" + object_key(sha256, content_type)
        self.objects[key] = data
        return key

    def check(self) -> None:
        if self.fail:
            raise ConnectionError("minio down")

def candidate(url: str, title: str = "Doc", source_type: str = "UTILITY",
              **extra: Any) -> dict[str, Any]:
    return {"url": url, "title": title, "source_type": source_type, "provider": "searxng",
            "snippet": "SNIPPET TEXT MUST NEVER BECOME EVIDENCE", **extra}
