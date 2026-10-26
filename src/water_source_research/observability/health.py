"""Reachability of the observability backends, probed from the API process (short timeouts,
cached for ``CACHE_SECONDS``), plus the export counters workers report in their heartbeat.

A backend that is switched off is DISABLED (not an error). A probe failure says the backend
was unreachable from here at that moment — it never stops research (exports are bounded and
dropped)."""

import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

CACHE_SECONDS = 30.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()

def _probe(name: str, url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            ok, detail = 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        ok, detail = False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        ok, detail = False, type(getattr(exc, "reason", exc)).__name__
    return {"backend": name, "enabled": True, "status": "UP" if ok else "DOWN",
            "detail": detail, "latency_ms": round((time.monotonic() - started) * 1000),
            "checked_at": datetime.now(UTC).isoformat(), "url": url}

def _cached(name: str, url: str, timeout: float) -> dict[str, Any]:
    with _lock:
        hit = _cache.get(name)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS and hit[1]["url"] == url:
            return hit[1]
    result = _probe(name, url, timeout)
    with _lock:
        _cache[name] = (time.monotonic(), result)
    return result

def backends(settings: Any = None) -> list[dict[str, Any]]:
    if settings is None:
        from water_source_research.core.config import get_settings
        settings = get_settings()
    obs = settings.observability
    t = obs.health_probe_timeout_seconds
    out = []
    if obs.otel_enabled and obs.trace_exporter == "otlp":
        out.append(_cached("otel_collector", obs.collector_health_url, t))
        out.append(_cached("jaeger", obs.jaeger_api_url.rstrip("/") + "/api/services", t))
    else:
        out += [{"backend": n, "enabled": False, "status": "DISABLED",
                 "detail": "tracing export is off" if not obs.otel_enabled
                 else f"exporter={obs.trace_exporter}"} for n in ("otel_collector", "jaeger")]
    if obs.langfuse_enabled and settings.langfuse_public_key:
        host = (settings.langfuse_host or "https://cloud.langfuse.com").rstrip("/")
        out.append(_cached("langfuse", host + "/api/public/health", t))
    else:
        out.append({"backend": "langfuse", "enabled": False, "status": "DISABLED",
                    "detail": "not configured (OBSERVABILITY__LANGFUSE_ENABLED and keys)"})
    return out

def clear_cache() -> None:
    with _lock:
        _cache.clear()
