"""Safe document fetching for acquisition: the only way acquisition touches the network.

- Only http/https on the default ports; no credentials in URLs.
- SSRF protection: every hostname (including each redirect hop) is resolved and rejected if
  any address is private, loopback, link-local, multicast, reserved or unspecified.
  Limitation: the address is re-resolved by the HTTP client at connect time, so a DNS
  rebinding attack between the check and the connect is not fully prevented.
- Redirects are followed manually (bounded) so each hop is validated.
- robots.txt is honoured per host (cached for the fetcher's lifetime); 401/403 is reported as
  AUTH_REQUIRED and never worked around.
- Bodies are streamed and cut off at a size cap; one request at a time per host with a
  minimum interval, plus a global in-flight cap.
"""

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from .config import AcquisitionSettings

Resolver = Callable[[str, int], Awaitable[list[str]]]

class FetchErrorKind(StrEnum):
    INVALID_URL = "INVALID_URL"
    BLOCKED_ADDRESS = "BLOCKED_ADDRESS"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    TOO_LARGE = "TOO_LARGE"
    HTTP_STATUS = "HTTP_STATUS"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    PARSE_ERROR = "PARSE_ERROR"

class FetchError(Exception):
    def __init__(self, kind: FetchErrorKind, message: str) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.message = message

@dataclass(slots=True)
class FetchedResponse:
    requested_url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: str  # ISO 8601 UTC

async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]

def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified or not ip.is_global)

_DEFAULT_PORTS = {"http": 80, "https": 443}

def validate_url(url: str) -> tuple[str, str, int]:
    """(scheme, host, port) for an acceptable URL, else FetchError(INVALID_URL)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError(FetchErrorKind.INVALID_URL, str(exc)) from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise FetchError(FetchErrorKind.INVALID_URL, f"scheme {scheme!r} not allowed")
    if not parts.hostname:
        raise FetchError(FetchErrorKind.INVALID_URL, "no host")
    if parts.username or parts.password:
        raise FetchError(FetchErrorKind.INVALID_URL, "credentials in URL not allowed")
    port = port or _DEFAULT_PORTS[scheme]
    if port not in (80, 443):
        raise FetchError(FetchErrorKind.INVALID_URL, f"port {port} not allowed")
    return scheme, parts.hostname.lower(), port

class SafeFetcher:
    def __init__(
        self,
        settings: AcquisitionSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = system_resolver,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.s = settings or AcquisitionSettings()
        self._client = client
        self._resolver = resolver
        self._clock = clock
        self._sleep = sleep
        self._global = asyncio.Semaphore(self.s.max_concurrency)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._resolved: dict[str, bool] = {}

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.s.timeout_seconds, follow_redirects=False,
                headers={"User-Agent": self.s.user_agent})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def fetch(self, url: str, max_bytes: int) -> FetchedResponse:
        """GET ``url`` safely (see module docstring). Raises FetchError."""
        requested = url
        for _ in range(self.s.max_redirects + 1):
            await self._check_host(url)
            if self.s.respect_robots and not await self._robots_allow(url):
                raise FetchError(FetchErrorKind.ROBOTS_DISALLOWED, url)
            status, headers, body, final = await self._get(url, max_bytes)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise FetchError(FetchErrorKind.HTTP_STATUS, f"{status} without Location")
                url = urljoin(final, location)
                continue
            if status in (401, 403):
                raise FetchError(FetchErrorKind.AUTH_REQUIRED, f"HTTP {status}")
            if status >= 400:
                raise FetchError(FetchErrorKind.HTTP_STATUS, f"HTTP {status}")
            return FetchedResponse(requested, final, status, headers.get("content-type", ""),
                                   body, datetime.now(UTC).isoformat(timespec="seconds"))
        raise FetchError(FetchErrorKind.TOO_MANY_REDIRECTS, f"> {self.s.max_redirects}")

    async def _check_host(self, url: str) -> None:
        _, host, port = validate_url(url)
        if host in self._resolved:
            if not self._resolved[host]:
                raise FetchError(FetchErrorKind.BLOCKED_ADDRESS, host)
            return
        try:
            ipaddress.ip_address(host.strip("[]"))
            addresses = [host.strip("[]")]
        except ValueError:
            try:
                addresses = await self._resolver(host, port)
            except (OSError, UnicodeError) as exc:
                raise FetchError(FetchErrorKind.NETWORK, f"DNS: {exc}") from None
        ok = bool(addresses) and all(is_public_address(a) for a in addresses)
        self._resolved[host] = ok
        if not ok:
            raise FetchError(FetchErrorKind.BLOCKED_ADDRESS, host)

    async def _get(self, url: str, max_bytes: int) -> tuple[int, httpx.Headers, bytes, str]:
        host = urlsplit(url).hostname or ""
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock, self._global:  # host first: don't hold a global slot while queued
            wait = self._host_last.get(host, -1e9) + self.s.per_host_min_interval_seconds \
                - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._host_last[host] = self._clock()
            try:
                async with self.client().stream(
                        "GET", url, headers={"User-Agent": self.s.user_agent},
                        timeout=self.s.timeout_seconds) as r:
                    if r.is_redirect or r.status_code >= 400:
                        return r.status_code, r.headers, b"", str(r.url)
                    length = r.headers.get("content-length")
                    if length and length.isdigit() and int(length) > max_bytes:
                        raise FetchError(FetchErrorKind.TOO_LARGE,
                                         f"{length} bytes > {max_bytes}")
                    chunks: list[bytes] = []
                    size = 0
                    async for part in r.aiter_bytes():
                        size += len(part)
                        if size > max_bytes:
                            raise FetchError(FetchErrorKind.TOO_LARGE, f"> {max_bytes} bytes")
                        chunks.append(part)
                    return r.status_code, r.headers, b"".join(chunks), str(r.url)
            except httpx.TimeoutException:
                raise FetchError(FetchErrorKind.TIMEOUT, url) from None
            except httpx.HTTPError as exc:
                raise FetchError(FetchErrorKind.NETWORK, f"{type(exc).__name__}") from None

    async def _robots_allow(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._robots:
                self._robots[origin] = await self._load_robots(origin)
        parser = self._robots[origin]
        if parser is None:  # robots.txt unreachable with a server error: stay away
            return False
        return parser.can_fetch(self.s.user_agent, url)

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        parser = RobotFileParser()
        robots_url = origin + "/robots.txt"
        try:
            for _ in range(self.s.max_redirects + 1):
                await self._check_host(robots_url)
                status, headers, body, final = await self._get(robots_url, 500_000)
                if status in (301, 302, 303, 307, 308) and headers.get("location"):
                    robots_url = urljoin(final, headers["location"])
                    continue
                break
            else:
                status = 404
        except FetchError as exc:
            if exc.kind in (FetchErrorKind.BLOCKED_ADDRESS, FetchErrorKind.INVALID_URL):
                raise
            return None
        if status >= 500:
            return None
        if status >= 400:  # no robots.txt (or not readable): everything allowed
            parser.parse([])
            return parser
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        return parser
