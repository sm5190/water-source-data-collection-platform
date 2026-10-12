"""Shared HTTP plumbing for discovery providers: timeouts, throttling, retries, caching.

Every provider talks to its API through one ``ProviderHttpClient``. Failures surface as a
typed ``ProviderError`` so the discovery service can isolate them per provider. Credentials
passed as query params are listed in ``secret_params``: they are sent, but never part of a
cache key, an error message, or a span attribute.
"""

import asyncio
import json
import random
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any
import httpx
from water_source_research.observability.otel import get_tracer
from .config import DiscoverySettings

USER_AGENT = "water-source-research/0.1 (discovery)"

class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    HTTP = "http_error"
    MALFORMED = "malformed_response"
    UNCONFIGURED = "unconfigured"

class ProviderError(Exception):
    def __init__(self, provider: str, kind: ProviderErrorKind, message: str = "",
                 status: int | None = None) -> None:
        self.provider = provider
        self.kind = kind
        self.status = status
        super().__init__(f"{provider}: {kind.value}" + (f" ({message})" if message else ""))

class ResponseCache:
    """In-process TTL + LRU cache of parsed JSON responses."""

    def __init__(self, ttl_seconds: float, max_entries: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if self.clock() >= expires:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        self._data[key] = (self.clock() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (time.time() if now is None else now))

class _Limiter:
    def __init__(self, concurrency: int) -> None:
        self.semaphore = asyncio.Semaphore(concurrency)
        self.lock = asyncio.Lock()
        self.last_start = float("-inf")

class ProviderHttpClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        settings: DiscoverySettings,
        *,
        headers: Mapping[str, str] | None = None,
        default_params: Mapping[str, str] | None = None,
        secret_params: frozenset[str] = frozenset(),
        cache: ResponseCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.settings = settings
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        self.default_params = {k: v for k, v in (default_params or {}).items() if v}
        self.secret_params = secret_params
        self.cache = cache if cache is not None else ResponseCache(
            settings.cache_ttl_seconds, settings.cache_max_entries)
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        limits = settings.limits_for(provider)
        self.max_concurrency = limits.max_concurrency
        self.min_interval = limits.min_interval_seconds
        # asyncio primitives are bound to one event loop; keep one limiter per loop.
        self._limiters: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Limiter] = \
            weakref.WeakKeyDictionary()

    def cache_key(self, path: str, params: Mapping[str, Any]) -> str:
        public = {k: v for k, v in params.items() if k not in self.secret_params}
        return json.dumps([self.provider, path, sorted(public.items())], default=str)

    async def get_json(self, path: str, params: Mapping[str, Any] | None = None, *,
                       not_found_ok: bool = False) -> Any | None:
        """GET ``base_url + path`` and return parsed JSON (``None`` for a tolerated 404)."""
        params = {**self.default_params, **(params or {})}
        key = self.cache_key(path, params)
        with get_tracer().start_as_current_span(f"discovery.{self.provider}.http") as span:
            span.set_attribute("discovery.provider", self.provider)
            span.set_attribute("discovery.path", path)
            cached = self.cache.get(key)
            span.set_attribute("discovery.cache_hit", cached is not None)
            if cached is not None:
                return cached
            data = await self._get_with_retries(path, params, not_found_ok, span)
            if data is not None:
                self.cache.set(key, data)
            return data

    async def _get_with_retries(self, path: str, params: dict[str, Any], not_found_ok: bool,
                                span) -> Any | None:
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            span.set_attribute("discovery.attempts", attempt + 1)
            retry_after: float | None = None
            try:
                response = await self._send(path, params)
            except httpx.TimeoutException:
                error = ProviderError(self.provider, ProviderErrorKind.TIMEOUT,
                                      f"no response within {self.settings.timeout_seconds}s")
            except httpx.TransportError as exc:
                error = ProviderError(self.provider, ProviderErrorKind.NETWORK,
                                      type(exc).__name__)
            else:
                status = response.status_code
                span.set_attribute("http.status_code", status)
                if status == 404 and not_found_ok:
                    return None
                if status == 429:
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    error = ProviderError(self.provider, ProviderErrorKind.RATE_LIMITED,
                                          "HTTP 429", status)
                elif status >= 500:
                    error = ProviderError(self.provider, ProviderErrorKind.HTTP,
                                          f"HTTP {status}", status)
                elif status >= 400:
                    raise ProviderError(self.provider, ProviderErrorKind.HTTP,
                                        f"HTTP {status} for {path}", status)
                else:
                    try:
                        return response.json()
                    except ValueError:
                        raise ProviderError(self.provider, ProviderErrorKind.MALFORMED,
                                            "response is not JSON", status) from None
            if attempt == attempts - 1:
                span.set_attribute("discovery.error", error.kind.value)
                raise error
            await self.sleep(self._backoff(attempt, retry_after))
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        cap = self.settings.backoff_max_seconds
        if retry_after is not None:
            return min(retry_after, cap)
        # exponential backoff with full jitter
        return self.jitter() * min(cap, self.settings.backoff_base_seconds * 2 ** attempt)

    async def _send(self, path: str, params: dict[str, Any]) -> httpx.Response:
        limiter = self._limiter()
        async with limiter.semaphore:
            if self.min_interval > 0:
                async with limiter.lock:
                    wait = limiter.last_start + self.min_interval - self.clock()
                    if wait > 0:
                        await self.sleep(wait)
                    limiter.last_start = self.clock()
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds,
                                         headers=self.headers, transport=self.transport,
                                         follow_redirects=True) as client:
                return await client.get(f"{self.base_url}{path}", params=params)

    def _limiter(self) -> _Limiter:
        loop = asyncio.get_running_loop()
        limiter = self._limiters.get(loop)
        if limiter is None:
            limiter = self._limiters[loop] = _Limiter(self.max_concurrency)
        return limiter

def expect_items(provider: str, payload: Any, *path: str, missing_ok: bool = False) -> list[dict]:
    """Walk ``payload[path...]`` and return its dict items, or raise MALFORMED.

    ``missing_ok``: an absent/null list means "no results" (some APIs omit it when empty).
    Non-dict entries inside the list are skipped rather than failing the whole response.
    """
    node = payload
    for key in path:
        if not isinstance(node, dict):
            raise ProviderError(provider, ProviderErrorKind.MALFORMED,
                                f"expected object at {key!r}")
        node = node.get(key)
    if node is None and missing_ok:
        return []
    if not isinstance(node, list):
        raise ProviderError(provider, ProviderErrorKind.MALFORMED,
                            f"expected list at {'.'.join(path)!r}")
    return [item for item in node if isinstance(item, dict)]

def text(value: Any) -> str | None:
    """A stripped string, or None for non-strings/blank values."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
