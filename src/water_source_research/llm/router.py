"""The LLM router: every model call in the pipeline is built here.

``build_structured_llm`` selects exactly one backend from ``Settings.llm_provider`` (``vt`` by
default, ``openai`` once paid access exists) and wraps it in ``ReliableStructuredLLM``, which
owns rate limiting, response caching, retries and structured-output validation. There is no
fallback between providers: a VT failure surfaces as an ``LLMError`` and never reaches OpenAI.

Rate limiter and cache are shared per process (one limiter per provider endpoint), so the
async graph and threaded workers (distillation runs via ``asyncio.to_thread``) draw from the
same budget. They are process-local: with N worker processes, divide the limits by N.
"""

import asyncio
import hashlib
import json
import random
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar
from pydantic import BaseModel, ValidationError
from water_source_research.discovery.http import ResponseCache
from water_source_research.observability.otel import get_tracer
from .base import Completion, LLMError, LLMErrorKind
from .config import LLMSettings
from .embeddings import EmbeddingClient, OpenAICompatibleEmbeddingClient
from .vt_client import VTChatBackend

T = TypeVar("T", bound=BaseModel)

class Backend(Protocol):
    provider: str
    model: str
    base_url: str

    def complete(self, messages: list[dict[str, str]],
                 schema: type[BaseModel]) -> Completion: ...

    async def acomplete(self, messages: list[dict[str, str]],
                        schema: type[BaseModel]) -> Completion: ...

class RateLimiter:
    """Sliding-window requests/minute + in-flight cap, safe across threads and event loops.

    ``try_acquire`` never blocks; callers sleep (``time.sleep`` or ``asyncio.sleep``) for the
    returned number of seconds and try again.
    """

    POLL_SECONDS = 0.05  # re-check interval while at the concurrency cap

    def __init__(self, requests_per_minute: int, max_concurrency: int, *,
                 clock: Callable[[], float] = time.monotonic, window: float = 60.0) -> None:
        self.requests_per_minute = requests_per_minute
        self.max_concurrency = max_concurrency
        self.clock = clock
        self.window = window
        self._lock = threading.Lock()
        self._starts: deque[float] = deque()
        self.in_flight = 0

    def try_acquire(self) -> float:
        with self._lock:
            now = self.clock()
            while self._starts and self._starts[0] <= now - self.window:
                self._starts.popleft()
            if self.in_flight >= self.max_concurrency:
                return self.POLL_SECONDS
            if len(self._starts) >= self.requests_per_minute:
                return max(self._starts[0] + self.window - now, self.POLL_SECONDS)
            self._starts.append(now)
            self.in_flight += 1
            return 0.0

    def release(self) -> None:
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)

    def acquire(self, sleep: Callable[[float], None] = time.sleep) -> None:
        while (wait := self.try_acquire()) > 0:
            sleep(wait)

    async def aacquire(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        while (wait := self.try_acquire()) > 0:
            await sleep(wait)

class _LockedCache:
    """``ResponseCache`` guarded for use from worker threads and the event loop."""

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._cache = ResponseCache(ttl_seconds, max_entries)
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache.set(key, value)

    def __len__(self) -> int:
        return len(self._cache)

_shared_lock = threading.Lock()
_limiters: dict[tuple[str, str], RateLimiter] = {}
_caches: dict[str, _LockedCache] = {}

def shared_limiter(provider: str, base_url: str, settings: LLMSettings) -> RateLimiter:
    with _shared_lock:
        key = (provider, base_url)
        if key not in _limiters:
            _limiters[key] = RateLimiter(settings.requests_per_minute, settings.max_concurrency)
        return _limiters[key]

def shared_cache(settings: LLMSettings) -> _LockedCache:
    with _shared_lock:
        if "llm" not in _caches:
            _caches["llm"] = _LockedCache(settings.cache_ttl_seconds, settings.cache_max_entries)
        return _caches["llm"]

def reset_shared_state() -> None:
    """Drop the process-wide limiters and cache (tests, settings reloads)."""
    with _shared_lock:
        _limiters.clear()
        _caches.clear()

class ReliableStructuredLLM:
    """Structured calls through one backend: cache → rate limit → call → validate/repair.

    Transient errors (429, timeouts, network, 5xx) are retried with exponential backoff and
    full jitter, honouring the server's wait hint; auth, bad-request and too-large errors are
    raised at once. Output that doesn't validate against ``schema`` is sent back with the
    validation errors up to ``structured_max_attempts`` times, then raises ``LLMError(SCHEMA)``.
    Nothing unvalidated is ever returned or cached.
    """

    def __init__(self, backend: Backend, *, task: str, prompt_version: str,
                 settings: LLMSettings, limiter: RateLimiter, cache: Any,
                 sleep: Callable[[float], None] = time.sleep,
                 asleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 jitter: Callable[[], float] = random.random,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.provider = backend.provider
        self.model = backend.model
        self.task = task
        self.prompt_version = prompt_version
        self.settings = settings
        self.limiter = limiter
        self.cache = cache
        self.sleep = sleep
        self.asleep = asleep
        self.jitter = jitter
        self.clock = clock

    def cache_key(self, system: str, user: str, schema: type[BaseModel]) -> str:
        schema_hash = hashlib.sha256(json.dumps(schema.model_json_schema(), sort_keys=True)
                                     .encode("utf-8")).hexdigest()
        payload = [self.provider, self.backend.base_url, self.model, self.task,
                   self.prompt_version, schema.__name__, schema_hash, system, user]
        return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        with self._span() as stats:
            key = self.cache_key(system, user, schema)
            if (hit := self._cached(key, schema, stats)) is not None:
                return hit
            messages = _messages(system, user)
            for repair in range(self.settings.structured_max_attempts + 1):
                completion = self._call(messages, schema, stats)
                result, messages = self._validate(completion, schema, messages, repair, stats)
                if result is not None:
                    self.cache.set(key, result.model_dump(mode="json"))
                    return result
            raise AssertionError("unreachable")

    async def astructured(self, system: str, user: str, schema: type[T]) -> T:
        with self._span() as stats:
            key = self.cache_key(system, user, schema)
            if (hit := self._cached(key, schema, stats)) is not None:
                return hit
            messages = _messages(system, user)
            for repair in range(self.settings.structured_max_attempts + 1):
                completion = await self._acall(messages, schema, stats)
                result, messages = self._validate(completion, schema, messages, repair, stats)
                if result is not None:
                    self.cache.set(key, result.model_dump(mode="json"))
                    return result
            raise AssertionError("unreachable")

    # --- internals ----------------------------------------------------------------------

    @contextmanager
    def _span(self) -> Iterator["_CallStats"]:
        with get_tracer().start_as_current_span("llm.call") as span:
            span.set_attribute("llm.provider", self.provider)
            span.set_attribute("llm.model", self.model)
            span.set_attribute("llm.task", self.task)
            span.set_attribute("llm.prompt_version", self.prompt_version)
            stats = _CallStats(span, self.provider, self.model)
            try:
                yield stats
            finally:
                stats.flush()

    def _cached(self, key: str, schema: type[T], stats: "_CallStats") -> T | None:
        cached = self.cache.get(key)
        stats.cache_hit = cached is not None
        stats.span.set_attribute("llm.cache_hit", cached is not None)
        return schema.model_validate(cached) if cached is not None else None

    def _call(self, messages: list[dict[str, str]], schema: type[BaseModel],
              stats: "_CallStats") -> Completion:
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            self.limiter.acquire(self.sleep)
            started = self.clock()
            try:
                return stats.record(self.backend.complete(messages, schema),
                                    self.clock() - started)
            except LLMError as exc:
                if not stats.failed(exc) or attempt == attempts - 1:
                    raise
                retry_after = exc.retry_after
            finally:
                self.limiter.release()
            self.sleep(self._backoff(attempt, retry_after))
        raise AssertionError("unreachable")

    async def _acall(self, messages: list[dict[str, str]], schema: type[BaseModel],
                     stats: "_CallStats") -> Completion:
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            await self.limiter.aacquire(self.asleep)
            started = self.clock()
            try:
                return stats.record(await self.backend.acomplete(messages, schema),
                                    self.clock() - started)
            except LLMError as exc:
                if not stats.failed(exc) or attempt == attempts - 1:
                    raise
                retry_after = exc.retry_after
            finally:
                self.limiter.release()
            await self.asleep(self._backoff(attempt, retry_after))
        raise AssertionError("unreachable")

    def _validate(self, completion: Completion, schema: type[T],
                  messages: list[dict[str, str]], repair: int,
                  stats: "_CallStats") -> tuple[T | None, list[dict[str, str]]]:
        """(result, messages): a validated result, or the conversation for a repair turn."""
        try:
            if completion.parsed is not None:
                return schema.model_validate(completion.parsed.model_dump()), messages
            if not completion.text:
                raise ValueError("the reply was empty")
            return schema.model_validate_json(completion.text), messages
        except (ValidationError, ValueError) as exc:
            problem = _describe(exc)
            if repair >= self.settings.structured_max_attempts:
                stats.error = LLMErrorKind.SCHEMA.value
                raise LLMError(self.provider, LLMErrorKind.SCHEMA,
                               f"invalid {schema.__name__} after {repair} repair(s): "
                               f"{problem[:300]}") from None
            stats.repairs = repair + 1
            return None, [*messages,
                          {"role": "assistant", "content": completion.text or ""},
                          {"role": "user", "content":
                           "Your previous reply did not match the required JSON schema: "
                           f"{problem}\nReply again with only the corrected JSON object."}]

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        cap = self.settings.backoff_max_seconds
        if retry_after is not None:
            return min(retry_after, cap)
        return self.jitter() * min(cap, self.settings.backoff_base_seconds * 2 ** attempt)

class _CallStats:
    """Per-call telemetry, written to the ``llm.call`` span (provider/model/tokens/latency)."""

    def __init__(self, span, provider: str = "", model: str = "") -> None:
        self.span = span
        self.provider = provider
        self.model = model
        self.cache_hit = False
        self.attempts = 0
        self.repairs = 0
        self.latency_ms = 0
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.error: str | None = None
        self.status: int | None = None

    def record(self, completion: Completion, seconds: float) -> Completion:
        self.attempts += 1
        self.latency_ms += round(seconds * 1000)
        if completion.prompt_tokens is not None:
            self.prompt_tokens = (self.prompt_tokens or 0) + completion.prompt_tokens
        if completion.completion_tokens is not None:
            self.completion_tokens = (self.completion_tokens or 0) + completion.completion_tokens
        return completion

    def failed(self, exc: LLMError) -> bool:
        """Record a failed attempt; True if the error kind is worth retrying."""
        self.attempts += 1
        self.error = exc.kind.value
        self.status = exc.status
        return exc.retryable

    def flush(self) -> None:
        values = {"llm.attempts": self.attempts, "llm.repairs": self.repairs,
                  "llm.latency_ms": self.latency_ms, "llm.prompt_tokens": self.prompt_tokens,
                  "llm.completion_tokens": self.completion_tokens, "llm.error": self.error,
                  "http.status_code": self.status}
        for name, value in values.items():
            if value is not None:
                self.span.set_attribute(name, value)
        log = llm_call_log.get()
        if log is not None:
            log.append({"provider": self.provider, "model": self.model,
                        "cache_hit": self.cache_hit, "requests": self.attempts,
                        "repairs": self.repairs, "latency_ms": self.latency_ms,
                        "prompt_tokens": self.prompt_tokens or 0,
                        "completion_tokens": self.completion_tokens or 0,
                        "error": self.error})

# Optional per-task log of model calls: a caller (e.g. a graph node) sets a list here to learn
# what its calls cost; each structured call appends one record when it finishes.
llm_call_log: ContextVar[list[dict[str, Any]] | None] = ContextVar("llm_call_log",
                                                                   default=None)

def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        # Locations and messages only; never echo the (possibly long) input back.
        parts = [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
                 for e in exc.errors(include_url=False)[:10]]
        return "; ".join(parts)
    return str(exc)

# --- builders -----------------------------------------------------------------------------

def build_backend(settings: Any, model: str | None = None, *, http_client: Any = None,
                  async_http_client: Any = None) -> Backend:
    """The selected provider's backend. Raises ``LLMError(UNCONFIGURED)`` if it lacks
    credentials; never builds the other provider."""
    provider = settings.llm_provider
    if provider == "vt":
        return VTChatBackend(settings.vt_llm_base_url, settings.vt_llm_api_key,
                             model or settings.vt_llm_model, settings.llm,
                             http_client=http_client, async_http_client=async_http_client)
    if provider == "openai":
        from .openai_reasoner import OpenAIResponsesBackend
        return OpenAIResponsesBackend(settings.openai_api_key, model or settings.openai_model,
                                      settings.llm, http_client=http_client,
                                      async_http_client=async_http_client)
    raise ValueError(f"Unknown LLM_PROVIDER {provider!r}")

def build_structured_llm(settings: Any, *, task: str, prompt_version: str,
                         model: str | None = None, http_client: Any = None,
                         async_http_client: Any = None, limiter: RateLimiter | None = None,
                         cache: Any = None, **kwargs: Any) -> ReliableStructuredLLM:
    """A structured-output LLM for ``task`` on the configured provider.

    ``model`` overrides the provider's default model for this task. ``prompt_version`` is
    part of the cache key; bump it with the prompt or response schema.
    """
    backend = build_backend(settings, model, http_client=http_client,
                            async_http_client=async_http_client)
    llm_settings: LLMSettings = settings.llm
    return ReliableStructuredLLM(
        backend, task=task, prompt_version=prompt_version, settings=llm_settings,
        limiter=limiter or shared_limiter(backend.provider, backend.base_url, llm_settings),
        cache=cache if cache is not None else shared_cache(llm_settings), **kwargs)

def build_embedding_client(settings: Any, *, http_client: Any = None) -> EmbeddingClient:
    """Embeddings on the configured provider (VT: Qwen3-Embedding-4B; OpenAI:
    ``procedure_memory.embedding_model``). Raises ``LLMError(UNCONFIGURED)`` without
    credentials."""
    if settings.llm_provider == "vt":
        if not (settings.vt_llm_base_url and settings.vt_llm_api_key
                and settings.vt_llm_embedding_model):
            raise LLMError("vt", LLMErrorKind.UNCONFIGURED,
                           "set VT_LLM_BASE_URL, VT_LLM_API_KEY and VT_LLM_EMBEDDING_MODEL")
        return OpenAICompatibleEmbeddingClient(
            settings.vt_llm_api_key, settings.vt_llm_embedding_model, provider="vt",
            base_url=settings.vt_llm_base_url,
            query_instruction=settings.llm.embedding_query_instruction,
            timeout=settings.llm.timeout_seconds, max_retries=settings.llm.max_retries,
            http_client=http_client)
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise LLMError("openai", LLMErrorKind.UNCONFIGURED, "set OPENAI_API_KEY")
        return OpenAICompatibleEmbeddingClient(
            settings.openai_api_key, settings.procedure_memory.embedding_model,
            provider="openai", timeout=settings.llm.timeout_seconds,
            max_retries=settings.llm.max_retries, http_client=http_client)
    raise ValueError(f"Unknown LLM_PROVIDER {settings.llm_provider!r}")
