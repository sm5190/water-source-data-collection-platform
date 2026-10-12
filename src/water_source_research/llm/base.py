"""Provider-independent LLM interfaces and errors.

Both providers (VT ARC and OpenAI) are reached through the ``openai`` SDK, so one classifier
turns SDK exceptions into a typed ``LLMError`` that the router retries (or not) by kind.
"""

import asyncio
import threading
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeVar
import openai
from pydantic import BaseModel
from water_source_research.discovery.http import parse_retry_after

T = TypeVar("T", bound=BaseModel)

class StructuredLLM(Protocol):
    """Synchronous structured-output model call (used by sync workers, e.g. distillation)."""

    model: str

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...

class AsyncStructuredLLM(Protocol):
    """Async structured-output model call (used by graph nodes, e.g. extraction)."""

    model: str

    async def astructured(self, system: str, user: str, schema: type[T]) -> T: ...

class LLMErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"   # 429: retried, honouring the server's wait hint
    TIMEOUT = "timeout"             # retried
    NETWORK = "network"             # retried
    SERVER = "server_error"         # 5xx: retried
    AUTH = "auth"                   # 401/403: never retried
    BAD_REQUEST = "bad_request"     # 400/404/422 (e.g. unknown model): never retried
    TOO_LARGE = "too_large"         # 413: never retried (shorten input or max tokens)
    SCHEMA = "schema"               # output never matched the response schema
    UNCONFIGURED = "unconfigured"   # selected provider has no credentials/model

RETRYABLE = frozenset({LLMErrorKind.RATE_LIMITED, LLMErrorKind.TIMEOUT, LLMErrorKind.NETWORK,
                       LLMErrorKind.SERVER})

class LLMError(Exception):
    def __init__(self, provider: str, kind: LLMErrorKind, message: str = "",
                 status: int | None = None, retry_after: float | None = None) -> None:
        self.provider = provider
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"{provider}: {kind.value}" + (f" ({message})" if message else ""))

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

class PerLoop[C]:
    """One object per running event loop.

    Async HTTP clients pool connections bound to the loop that opened them; reusing one after
    that loop closes (e.g. a second ``asyncio.run``) fails with "Event loop is closed".
    """

    def __init__(self, factory: Callable[[], C]) -> None:
        self._factory = factory
        self._items: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, C] = \
            weakref.WeakKeyDictionary()
        self._lock = threading.Lock()

    def get(self) -> C:
        loop = asyncio.get_running_loop()
        with self._lock:
            item = self._items.get(loop)
            if item is None:
                item = self._items[loop] = self._factory()
            return item

@dataclass(slots=True)
class Completion:
    """One model response: raw text (to validate) or an already-parsed object."""

    text: str | None = None
    parsed: BaseModel | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

def usage_tokens(usage: Any) -> tuple[int | None, int | None]:
    """(input, output) tokens from a Chat Completions or Responses usage object."""
    if usage is None:
        return None, None
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
    completion = getattr(usage, "completion_tokens", None) \
        or getattr(usage, "output_tokens", None)
    return prompt, completion

def classify_openai_error(provider: str, exc: Exception) -> LLMError:
    """Map an ``openai`` SDK exception to a typed ``LLMError`` (never leaks the API key)."""
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(provider, LLMErrorKind.TIMEOUT, "no response in time")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(provider, LLMErrorKind.NETWORK, type(exc).__name__)
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        detail = _error_detail(exc.body) or _text_detail(exc)
        if status == 429:
            return LLMError(provider, LLMErrorKind.RATE_LIMITED, detail or "HTTP 429", status,
                            _retry_after(exc))
        if status in (401, 403):
            # e.g. VT: "API access is restricted to the VT Campus VPN" (plain-text 403).
            return LLMError(provider, LLMErrorKind.AUTH,
                            f"HTTP {status}" + (f": {detail}" if detail else ""), status)
        if status == 413:
            return LLMError(provider, LLMErrorKind.TOO_LARGE, detail or "HTTP 413", status)
        if status >= 500:
            return LLMError(provider, LLMErrorKind.SERVER, f"HTTP {status}", status)
        return LLMError(provider, LLMErrorKind.BAD_REQUEST,
                        f"HTTP {status}" + (f": {detail}" if detail else ""), status)
    raise exc

def _retry_after(exc: "openai.APIStatusError") -> float | None:
    header = parse_retry_after(exc.response.headers.get("Retry-After"))
    if header is not None:
        return header
    # VT ARC puts the wait in the body (``error.retry_after_s``) instead of a header.
    for body in _bodies(exc.body):
        value = body.get("retry_after_s")
        if isinstance(value, int | float) and value >= 0:
            return float(value)
    return None

def _error_detail(body: object) -> str:
    for b in _bodies(body):
        for key in ("code", "reason", "message"):
            if isinstance(b.get(key), str) and b[key]:
                return b[key][:200]
    return ""

def _text_detail(exc: "openai.APIStatusError") -> str:
    """A plain-text error body (gateways often answer 403/5xx with text, not JSON)."""
    try:
        if "json" in exc.response.headers.get("content-type", ""):
            return ""
        return " ".join(exc.response.text.split())[:200]
    except Exception:  # noqa: BLE001 - best effort; the status code still classifies it
        return ""

def _bodies(body: object) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    if isinstance(body, Mapping):
        out.append(body)
        if isinstance(body.get("error"), Mapping):
            out.append(body["error"])
    return out
