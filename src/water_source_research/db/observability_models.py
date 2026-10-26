"""Worker lifecycle and crash records (operational, not research data).

``worker_events`` is append-only and keyed by a stable ``event_id`` so the supervisor's local
event files can be re-synced any number of times (insert-or-skip). ``facts`` holds what was
observed; ``inferred_cause``/``cause_confidence``/``rationale`` hold the interpretation, kept
apart so a later reading of the same facts can disagree.

``worker_instances`` is one row per worker process start, overwritten in place (latest
heartbeat and resource sample only), so its size is bounded by the number of starts.
"""

from datetime import datetime
from typing import Any
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, JSONType, utcnow

class WorkerInstanceRecord(Base):
    __tablename__ = "worker_instances"

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    supervisor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    host: Mapped[str | None] = mapped_column(String(255))
    pid: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sample: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    active_run_ids: Mapped[list[str] | None] = mapped_column(JSONType)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    termination_class: Mapped[str | None] = mapped_column(String(48))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class WorkerEventRecord(Base):
    __tablename__ = "worker_events"
    __table_args__ = (Index("ix_worker_events_kind_time", "kind", "occurred_at"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(48))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16))  # supervisor | worker
    instance_id: Mapped[str | None] = mapped_column(String(128), index=True)
    supervisor_id: Mapped[str | None] = mapped_column(String(64))
    pid: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    attempt: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    facts: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    inferred_cause: Mapped[str | None] = mapped_column(String(48))
    cause_confidence: Mapped[str | None] = mapped_column(String(16))
    rationale: Mapped[str | None] = mapped_column(String(2000))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RunTraceRecord(Base):
    """Which traces a run's executions produced (run_id stays the stable key; each attempt,
    recovery and review resume may be its own trace). Written when the span
    starts, so the ids survive even if the process dies before the span is exported."""

    __tablename__ = "run_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # SUBMIT | RUN | RECOVERY | REVIEW_RESUME
    attempt: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    instance_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ProviderUsageRecord(Base):
    """One external API call (an LLM call today; Firecrawl and other paid providers later):
    the single ledger for usage and cost. Nullable measures are UNKNOWN (never 0): tokens the
    provider didn't report, cost of a model without a configured price."""

    __tablename__ = "provider_usage"
    __table_args__ = (Index("ix_provider_usage_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(16))  # research | evaluation | procedure | other
    kind: Mapped[str] = mapped_column(String(16))  # LLM | CRAWL | SEARCH
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))  # model, or the API operation
    task: Mapped[str | None] = mapped_column(String(64))
    field_key: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    requests: Mapped[int] = mapped_column(Integer, default=0)  # HTTP requests (0 = cache hit)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_errors: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # kind -> count
    error: Mapped[str | None] = mapped_column(String(32))  # final error kind, if it failed
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    credits: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_basis: Mapped[str | None] = mapped_column(String(128))  # price-table key used
    trace_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
