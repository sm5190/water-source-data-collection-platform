"""What a model call may put on its span — identifiers, usage and keyed hashes by default.

- ``llm_context``: per-task context (run, field, round, re-extraction, attempt, bundle) set
  by the extraction path; the router copies it onto every ``llm.call`` span, so all providers
  get the same correlation without knowing about research.
- ``digest(text)``: HMAC-SHA256 of a prompt/response with a local installation secret, so
  equal prompts can be recognised across calls without the text being recoverable (a plain
  hash of a short prompt could be matched by guessing). The secret comes from
  ``OBSERVABILITY__HASH_KEY`` or ``<log_dir>/.telemetry-key`` (created on first use with
  owner-only permissions where the OS supports it; ``logs/`` is git-ignored).
- ``capture_content``: prompt/response text goes on spans ONLY with
  ``OBSERVABILITY__CAPTURE_LLM_CONTENT=truncated`` (opt-in, for debugging): redacted
  (secrets, emails, URL queries) and truncated. It can contain evidence passages from
  acquired documents; never enable it for shared backends without that in mind.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

llm_context: ContextVar[dict[str, Any] | None] = ContextVar("llm_context", default=None)

# Keys of llm_context copied onto llm.call spans (ids and counters only).
CONTEXT_ATTRIBUTES = {"run_id": "research.run_id", "field_key": "research.field_key",
                      "discovery_round": "research.discovery_round",
                      "reextraction": "extraction.reextraction",
                      "attempt": "extraction.attempt", "bundle_id": "context.bundle_id",
                      "sources": "extraction.sources", "task_kind": "llm.task_kind"}

@contextlib.contextmanager
def llm_call_context(**values: Any) -> Iterator[None]:
    current = dict(llm_context.get() or {})
    token = llm_context.set({**current, **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        llm_context.reset(token)

def context_attributes() -> dict[str, Any]:
    ctx = llm_context.get() or {}
    out: dict[str, Any] = {}
    for key, attr in CONTEXT_ATTRIBUTES.items():
        value = ctx.get(key)
        if isinstance(value, bool | int | float | str) and value != "":
            out[attr] = value
    if ctx.get("run_id"):
        out["session.id"] = str(ctx["run_id"])  # Langfuse session = research run
    return out

# --- keyed hashes -------------------------------------------------------------------------

_key_lock = threading.Lock()
_key: bytes | None = None

def hash_key(log_dir: str | None = None) -> bytes:
    global _key
    with _key_lock:
        if _key is not None:
            return _key
        env = os.environ.get("OBSERVABILITY__HASH_KEY")
        if env:
            _key = env.encode("utf-8")
            return _key
        if log_dir is None:
            from water_source_research.core.config import get_settings
            log_dir = get_settings().observability.log_dir
        path = Path(log_dir) / ".telemetry-key"
        try:
            if path.exists():
                _key = bytes.fromhex(path.read_text(encoding="ascii").strip())
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                key = secrets.token_bytes(32)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as f:
                    f.write(key.hex())
                with contextlib.suppress(OSError):
                    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
                _key = key
        except (OSError, ValueError):
            log.warning("no telemetry hash key available; using a per-process key")
            _key = secrets.token_bytes(32)
        return _key

def digest(text: str) -> str:
    mac = hmac.new(hash_key(), text.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{mac[:32]}"

# --- opt-in content -----------------------------------------------------------------------

_REDACT = [
    (re.compile(r"(sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{12,}|bearer\s+\S+)", re.IGNORECASE),
     "[REDACTED_SECRET]"),
    (re.compile(r"\b(api[_-]?key|access[_-]?token|password|secret|authorization)\b\s*[:=]\s*"
                r"\S+", re.IGNORECASE), r"\1=[REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"(https?://[^\s?#]+)[?#][^\s]*"), r"\1"),  # drop URL queries/fragments
]
MAX_CONTENT_CHARS = 2000

def redact(text: str) -> str:
    for pattern, replacement in _REDACT:
        text = pattern.sub(replacement, text)
    return text

def content_mode() -> str:
    from water_source_research.core.config import get_settings
    try:
        return get_settings().observability.capture_llm_content
    except Exception:  # noqa: BLE001
        return "off"

def record_content(span: Any, *, prompt: str | None = None, response: str | None = None,
                   mode: str | None = None) -> None:
    """Keyed hashes always; redacted, truncated text only when explicitly enabled."""
    mode = mode or content_mode()
    if prompt is not None:
        span.set_attribute("llm.prompt_hmac", digest(prompt))
        span.set_attribute("llm.prompt_chars", len(prompt))
    if response is not None:
        span.set_attribute("llm.response_hmac", digest(response))
        span.set_attribute("llm.response_chars", len(response))
    if mode != "truncated":
        return
    if prompt is not None:
        span.set_attribute("langfuse.observation.input",
                           json.dumps(redact(prompt)[:MAX_CONTENT_CHARS]))
    if response is not None:
        span.set_attribute("langfuse.observation.output",
                           json.dumps(redact(response)[:MAX_CONTENT_CHARS]))

# --- cost (only from a configured price table) ----------------------------------------------

_prices: dict[str, dict[str, float]] | None = None

def prices() -> dict[str, dict[str, float]]:
    global _prices
    if _prices is None:
        import yaml
        from water_source_research.core.paths import CONFIG_DIR
        path = CONFIG_DIR / "llm_prices.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        _prices = dict((data or {}).get("prices") or {})
    return _prices

def estimate_cost(provider: str, model: str, input_tokens: int | None,
                  output_tokens: int | None, cached_tokens: int | None) -> float | None:
    """USD from the price table; None (UNKNOWN) when the model has no price or usage is
    unknown. Never guessed."""
    price = prices().get(f"{provider}:{model}")
    if not price or input_tokens is None or output_tokens is None:
        return None
    cached = min(cached_tokens or 0, input_tokens)
    cached_rate = price.get("cached_input_per_1m", price.get("input_per_1m", 0.0))
    total = ((input_tokens - cached) * price.get("input_per_1m", 0.0)
             + cached * cached_rate + output_tokens * price.get("output_per_1m", 0.0)) / 1e6
    return round(total, 6)
