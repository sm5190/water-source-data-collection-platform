"""Trace context across processes and attempts, keyed by the stable research ``run_id``.

- ``carrier(span)`` / ``trusted_context(carrier)``: the W3C traceparent we store on the job
  row ourselves (submit, review resume) and the worker continues. It is rebuilt as a *local*
  parent, so its sampled flag is honoured; contexts from HTTP headers stay remote and can't
  force sampling (``otel.research_sampler``).
- ``TraceStore``: ``run_traces`` rows (run -> trace/span ids per attempt, recovery, review
  resume, evaluation), written when a span starts, so the ids survive a killed process
  whose spans were never exported.
- ``safe_url``: scheme://host/path only (no query, fragment or credentials) for attributes.
"""

import logging
from typing import Any
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link, NonRecordingSpan, SpanContext, TraceFlags
from water_source_research.schemas.events import strip_url

log = logging.getLogger(__name__)

def ids(span: Any) -> tuple[str | None, str | None, bool]:
    """(trace_id, span_id, sampled) of a span; (None, None, False) for an invalid one."""
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None, None, False
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}", bool(ctx.trace_flags.sampled)

def carrier(span: Any) -> dict[str, str] | None:
    trace_id, span_id, sampled = ids(span)
    if trace_id is None:
        return None
    return {"traceparent": f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"}

def _parse(traceparent: str | None) -> tuple[int, int, bool] | None:
    try:
        version, trace_id, span_id, flags = (traceparent or "").split("-")
        t, s = int(trace_id, 16), int(span_id, 16)
        if version != "00" or not t or not s:
            return None
        return t, s, bool(int(flags, 16) & 1)
    except ValueError:
        return None

def trusted_context(stored: dict[str, Any] | None) -> Context | None:
    """Parent context from a carrier WE stored (job row). None when absent/invalid."""
    parsed = _parse((stored or {}).get("traceparent"))
    if parsed is None:
        return None
    t, s, sampled = parsed
    span_ctx = SpanContext(t, s, is_remote=False,
                           trace_flags=TraceFlags(TraceFlags.SAMPLED if sampled else 0))
    return trace.set_span_in_context(NonRecordingSpan(span_ctx), Context())

def link(trace_id: str | None, span_id: str | None, **attrs: Any) -> Link | None:
    try:
        t, s = int(trace_id or "", 16), int(span_id or "", 16)
    except ValueError:
        return None
    if not t or not s:
        return None
    return Link(SpanContext(t, s, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)),
                {k: v for k, v in attrs.items() if v is not None})

def link_from(stored: dict[str, Any] | None, **attrs: Any) -> Link | None:
    parsed = _parse((stored or {}).get("traceparent"))
    if parsed is None:
        return None
    return link(f"{parsed[0]:032x}", f"{parsed[1]:016x}", **attrs)

def safe_url(url: str | None) -> str:
    return strip_url(url) if url else ""

class TraceStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, run_id: str, kind: str, span: Any, *, attempt: int | None = None,
               instance_id: str | None = None) -> None:
        """Soft: a failure is logged, never raised into the run."""
        from water_source_research.db.observability_models import RunTraceRecord as R
        trace_id, span_id, sampled = ids(span)
        if trace_id is None:
            return
        try:
            with Session(self.engine) as session, session.begin():
                session.add(R(run_id=run_id, kind=kind, attempt=attempt, trace_id=trace_id,
                              span_id=span_id, sampled=sampled, instance_id=instance_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not record trace ids for %s (%s)", run_id, type(exc).__name__)

    def list(self, run_id: str) -> list[dict[str, Any]]:
        from water_source_research.db.observability_models import RunTraceRecord as R
        with Session(self.engine) as session:
            rows = session.scalars(select(R).where(R.run_id == run_id)
                                   .order_by(R.created_at, R.id)).all()
            return [{"kind": r.kind, "attempt": r.attempt, "trace_id": r.trace_id,
                     "span_id": r.span_id, "sampled": r.sampled, "instance_id": r.instance_id,
                     "created_at": r.created_at} for r in rows]

    def latest(self, run_id: str, kinds: tuple[str, ...]) -> dict[str, Any] | None:
        rows = [r for r in self.list(run_id) if r["kind"] in kinds]
        return rows[-1] if rows else None
