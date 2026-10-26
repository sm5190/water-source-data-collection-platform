"""Optional Langfuse export of model calls, on the application's own TracerProvider.

Langfuse v5 is OpenTelemetry-native: ``Langfuse(tracer_provider=ours, ...)`` adds its span
processor to our provider instead of installing a second one, so Jaeger and Langfuse see the
same spans with the same trace ids. Enabled only with ``OBSERVABILITY__LANGFUSE_ENABLED=true``
and keys (``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``, ``LANGFUSE_HOST``); otherwise the
SDK is never imported. Self-hosting Langfuse (ClickHouse, Redis, S3) is not part of this.

- Hierarchy-preserving filter (``should_export_span``): Langfuse receives the model calls and
  their ancestor chain only — research.run / research.recovery -> graph.extract ->
  extraction.field -> llm.extract -> llm.call -> llm.request, and the evaluation judge and
  procedure distiller chains. Every exported span's parent is exported too (or it is a root).
- No duplicate observations: only ``llm.call`` of a fresh model invocation is a
  *generation*; per-attempt ``llm.request`` spans and cache hits are plain spans without usage.
- Privacy (``mask_otel_spans``): attributes outside an allowlist (ids, counters, versions,
  status, usage, keyed hashes) are deleted before export, and secret-shaped values are
  redacted. Prompt/response text reaches Langfuse only with
  ``OBSERVABILITY__CAPTURE_LLM_CONTENT=truncated`` (llm_telemetry.record_content).
- Unavailable Langfuse: exports run on the processor's thread with timeouts; failures are
  logged and dropped, never raised into a run.
"""

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

LANGFUSE_SPANS = frozenset({
    "research.run", "research.recovery", "graph.extract", "extraction.field", "llm.extract",
    "llm.call", "llm.request", "evaluation.run", "evaluation.judge",
    "procedure.distillation_job", "procedure.distill"})

# Attribute keys allowed on spans sent to Langfuse (prefix match).
ALLOWED_PREFIXES = (
    "research.", "extraction.", "context.status", "context.bundle_id", "llm.", "gen_ai.",
    "langfuse.observation.type", "langfuse.observation.model.", "langfuse.observation.usage",
    "langfuse.observation.cost", "langfuse.observation.level",
    "langfuse.observation.status_message", "langfuse.observation.metadata.",
    "langfuse.trace.name", "langfuse.environment", "langfuse.release", "langfuse.version",
    "langfuse.internal.", "session.id", "graph.run_id", "graph.discovery_round",
    "graph.fields", "evaluation.", "review.round", "http.status_code",
    "research_queue.attempt", "research_queue.outcome", "procedure.")
CONTENT_KEYS = ("langfuse.observation.input", "langfuse.observation.output")
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{12,}|bearer\s+\S+|"
                          r"[A-Za-z0-9+/_-]{48,})", re.IGNORECASE)

def should_export_span(span: Any) -> bool:
    return span.name in LANGFUSE_SPANS

def allowed(key: str, content_opt_in: bool) -> bool:
    if key in CONTENT_KEYS:
        return content_opt_in
    return key.startswith(ALLOWED_PREFIXES)

def mask_spans(content_opt_in: bool):
    """Export-stage masking for the Langfuse copy of each span."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    def mask(*, params: Any) -> Any:
        patches = {}
        for ident, span in params.spans.items():
            attrs = dict(span.attributes or {})
            drop = tuple(k for k in attrs if not allowed(k, content_opt_in))
            redact = {k: "[REDACTED]" for k, v in attrs.items()
                      if k not in drop and k not in CONTENT_KEYS and isinstance(v, str)
                      and not k.endswith("_hmac") and SECRET_VALUE.search(v)}
            if drop or redact:
                patches[ident] = OtelSpanPatch(delete_attributes=drop, set_attributes=redact)
        return MaskOtelSpansResult(span_patches=patches)
    return mask

def _mask_sdk_data(*, data: Any, **_: Any) -> Any:
    """Langfuse SDK-level mask (data set through SDK APIs, which this code doesn't use):
    redact strings recursively all the same."""
    from .llm_telemetry import redact
    if isinstance(data, str):
        return redact(data)
    if isinstance(data, dict):
        return {k: _mask_sdk_data(data=v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask_sdk_data(data=v) for v in data]
    return data

_client: Any = None

def configure_langfuse(provider: Any, settings: Any, *,
                       span_exporter: Any = None) -> Any:
    """Attach Langfuse to ``provider``. Returns the client, or None when disabled, keys are
    missing or the SDK isn't installed (``uv sync --extra observability``)."""
    global _client
    obs = settings.observability
    if not obs.langfuse_enabled:
        return None
    if span_exporter is None and not (settings.langfuse_public_key
                                      and settings.langfuse_secret_key):
        log.warning("Langfuse enabled but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are not set; "
                    "LLM observations go only to the tracing backend")
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        log.warning("Langfuse enabled but the SDK is not installed "
                    "(uv sync --extra observability)")
        return None
    _client = Langfuse(
        public_key=settings.langfuse_public_key or "pk-local-test",
        secret_key=settings.langfuse_secret_key or "sk-local-test",
        base_url=settings.langfuse_host or None,
        tracer_provider=provider,
        should_export_span=should_export_span,
        mask_otel_spans=mask_spans(obs.capture_llm_content == "truncated"),
        mask=_mask_sdk_data,
        environment=obs.environment,
        flush_interval=obs.langfuse_flush_interval_seconds,
        timeout=int(obs.export_timeout_seconds),
        span_exporter=span_exporter,
    )
    log.info("Langfuse export enabled (%s)", settings.langfuse_host or "default host")
    return _client

def shutdown_langfuse() -> None:
    global _client
    client, _client = _client, None
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        log.warning("Langfuse flush failed", exc_info=True)
