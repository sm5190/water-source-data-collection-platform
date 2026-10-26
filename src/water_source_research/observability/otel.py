"""The one OpenTelemetry configuration of every process (worker, API, scripts).

``configure_telemetry(role)`` installs a single global TracerProvider (idempotent; a provider
already installed, e.g. by tests, is left alone) when ``OBSERVABILITY__OTEL_ENABLED=true``;
otherwise nothing is installed and every span is a no-op. Exporting never affects research:
the batch processor's queue is bounded (spans beyond it are dropped), exports time out, and
exporter errors are logged by the SDK and discarded. ``shutdown_telemetry()`` flushes with a
bound. Spans of a worker that is killed are not flushed: crash evidence lives in the
supervisor's worker events, never in traces.

Sampling (``ResearchSampler``): a root span is sampled when it carries
``research.trace_requested=True`` (a run submitted with trace=true; set only from our own job
row) or by ``sample_ratio``. Children follow their local parent. A parent arriving from
outside (an HTTP ``traceparent``) does NOT decide: the ratio does, so an external header can
never force sampling (with ratio 0 nothing from outside is traced).
"""

import atexit
import json
import logging
import logging.handlers
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF, ALWAYS_ON, Decision, ParentBased, Sampler, SamplingResult, TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind
from opentelemetry.util.types import Attributes

log = logging.getLogger(__name__)

TRACE_REQUESTED = "research.trace_requested"
_lock = threading.Lock()
_provider: TracerProvider | None = None  # the provider this module installed, if any

class _SelectedOrRatio(Sampler):
    """Root decision: explicitly selected runs always, everything else by ratio."""

    def __init__(self, ratio: float) -> None:
        self.ratio = TraceIdRatioBased(ratio)

    def should_sample(self, parent_context: Context | None, trace_id: int, name: str,
                      kind: SpanKind | None = None, attributes: Attributes = None,
                      links: Sequence[Link] | None = None,
                      trace_state: Any = None) -> SamplingResult:
        if attributes and attributes.get(TRACE_REQUESTED) is True:
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes)
        return self.ratio.should_sample(parent_context, trace_id, name, kind, attributes,
                                        links, trace_state)

    def get_description(self) -> str:
        return f"SelectedOrRatio({self.ratio.rate})"

def research_sampler(ratio: float) -> Sampler:
    root = _SelectedOrRatio(ratio)
    return ParentBased(root=root,
                       remote_parent_sampled=root,  # external headers can't force sampling
                       remote_parent_not_sampled=root,
                       local_parent_sampled=ALWAYS_ON,
                       local_parent_not_sampled=ALWAYS_OFF)

class JsonLinesSpanExporter(SpanExporter):
    """Spans as JSON lines in a size-rotated file (bounded): offline local debugging and
    multi-process tests, no collector needed."""

    def __init__(self, path: Path, max_mb: float = 20.0, backups: int = 3) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._log = logging.getLogger(f"otel.file_exporter.{path}")
        self._log.propagate = False
        self._log.setLevel(logging.INFO)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=int(max_mb * 2**20), backupCount=backups, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._handler = handler
        self._log.addHandler(handler)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            for s in spans:
                self._log.info(json.dumps(span_record(s), default=str))
            self._handler.flush()
            return SpanExportResult.SUCCESS
        except Exception:  # noqa: BLE001 - never raise into the SDK
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._handler.close()
        self._log.removeHandler(self._handler)

class CountingExporter(SpanExporter):
    """Counts export outcomes of the wrapped exporter (the backend's health as seen from this
    process: published in the worker heartbeat and checked by alerts). Never raises."""

    def __init__(self, inner: SpanExporter, name: str) -> None:
        self.inner = inner
        self.name = name
        self.ok = 0
        self.failed = 0
        self.last_failure_at: str | None = None
        self.last_success_at: str | None = None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        from datetime import UTC, datetime
        try:
            result = self.inner.export(spans)
        except Exception:  # noqa: BLE001
            result = SpanExportResult.FAILURE
        now = datetime.now(UTC).isoformat()
        if result is SpanExportResult.SUCCESS:
            self.ok += 1
            self.last_success_at = now
        else:
            self.failed += 1
            self.last_failure_at = now
        return result

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)

    def stats(self) -> dict[str, Any]:
        return {"exporter": self.name, "exports_ok": self.ok, "exports_failed": self.failed,
                "last_failure_at": self.last_failure_at,
                "last_success_at": self.last_success_at}

_counting: CountingExporter | None = None

def exporter_stats() -> dict[str, Any] | None:
    """This process's span export health (None when nothing is exported)."""
    return _counting.stats() if _counting is not None else None

def span_record(s: ReadableSpan) -> dict[str, Any]:
    ctx = s.get_span_context()
    return {"name": s.name, "trace_id": f"{ctx.trace_id:032x}", "span_id": f"{ctx.span_id:016x}",
            "parent_span_id": f"{s.parent.span_id:016x}" if s.parent else None,
            "start": s.start_time, "end": s.end_time,
            "status": s.status.status_code.name, "attributes": dict(s.attributes or {}),
            "links": [{"trace_id": f"{lk.context.trace_id:032x}",
                       "span_id": f"{lk.context.span_id:016x}",
                       "attributes": dict(lk.attributes or {})} for lk in s.links],
            "service": (s.resource.attributes or {}).get("service.name")}

def telemetry_enabled() -> bool:
    return _provider is not None

def configure_telemetry(role: str, *, instance_id: str | None = None,
                        settings: Any = None) -> TracerProvider | None:
    """Install the process's tracer provider once. Returns it, or None when disabled (or
    when another provider was installed first, e.g. by tests)."""
    global _provider
    from water_source_research.core.config import get_settings
    full = settings or get_settings()
    s = full.observability
    with _lock:
        if _provider is not None:
            return _provider
        if not (s.otel_enabled or s.langfuse_enabled):
            return None
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider):
            log.info("a tracer provider is already installed; not replacing it")
            return None
        resource = Resource.create({
            "service.name": f"{full.otel_service_name}-{role}",
            "service.namespace": full.otel_service_name,
            "service.instance.id": instance_id or f"{role}-{os.getpid()}",
            "deployment.environment": s.environment,
            "service.version": _version()})
        provider = TracerProvider(resource=resource, sampler=research_sampler(s.sample_ratio))
        exporter = _exporter(full, role) if s.otel_enabled else None
        if exporter is not None:
            global _counting
            exporter = _counting = CountingExporter(exporter, s.trace_exporter)
            provider.add_span_processor(BatchSpanProcessor(
                exporter, max_queue_size=s.span_queue_size,
                max_export_batch_size=min(s.span_batch_size, s.span_queue_size),
                export_timeout_millis=int(s.export_timeout_seconds * 1000)))
        trace.set_tracer_provider(provider)
        _provider = provider
        from .langfuse import configure_langfuse
        try:
            configure_langfuse(provider, full)  # optional; shares this provider
        except Exception:
            log.warning("Langfuse could not be configured", exc_info=True)
        atexit.register(shutdown_telemetry)
        log.info("tracing enabled (%s, sample_ratio=%s, exporter=%s)", role, s.sample_ratio,
                 s.trace_exporter)
        return provider

def _exporter(full: Any, role: str) -> SpanExporter | None:
    s = full.observability
    if s.trace_exporter == "none":
        return None
    if s.trace_exporter == "file":
        path = Path(s.trace_file) if s.trace_file else (
            Path(s.log_dir) / "traces" / f"{role}-{os.getpid()}.jsonl")
        return JsonLinesSpanExporter(path, s.trace_file_max_mb, s.trace_file_backups)
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    endpoint = s.otlp_endpoint or full.otel_exporter_otlp_endpoint
    return OTLPSpanExporter(endpoint=endpoint, insecure=True,
                            timeout=int(s.export_timeout_seconds))

def shutdown_telemetry(timeout_seconds: float = 5.0) -> None:
    """Flush and stop exporting (bounded; safe to call more than once)."""
    global _provider
    provider = _provider
    if provider is None:
        return
    _provider = None
    from .langfuse import shutdown_langfuse
    shutdown_langfuse()
    try:
        provider.force_flush(int(timeout_seconds * 1000))
        provider.shutdown()
    except Exception:
        log.warning("telemetry shutdown failed", exc_info=True)

def _version() -> str:
    try:
        from importlib.metadata import version
        return version("water-source-research")
    except Exception:  # noqa: BLE001
        return "dev"

class TraceContextFilter(logging.Filter):
    """Adds ``trace_id``/``span_id`` of the current span to log records ('-' outside spans)."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = f"{ctx.trace_id:032x}" if ctx.is_valid else "-"
        record.span_id = f"{ctx.span_id:016x}" if ctx.is_valid else "-"
        return True

def current_ids() -> tuple[str | None, str | None]:
    """(trace_id, span_id) of the current span, if it is a real (valid) one."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"

def get_tracer():
    return trace.get_tracer("water_source_research")

def get_meter():
    return metrics.get_meter("water_source_research")
