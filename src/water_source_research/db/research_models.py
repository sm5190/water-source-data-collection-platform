"""Durable records of research runs and the trusted events they emitted.

These are the input to trajectory distillation. They describe what a run did; they are not
the scientific dataset and not procedure memory.
"""

from datetime import datetime
from typing import Any
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, JSONType, utcnow

class ResearchRunRecord(Base):
    __tablename__ = "research_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    city_name: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(255))
    workflow_version: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))  # COMPLETED | FAILED
    fields_total: Mapped[int] = mapped_column(Integer, default=0)
    fields_resolved: Mapped[int] = mapped_column(Integer, default=0)
    fields_conflicted: Mapped[int] = mapped_column(Integer, default=0)
    human_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    human_correction_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_claim_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    node: Mapped[str] = mapped_column(String(64))
    field_key: Mapped[str | None] = mapped_column(String(128))
    capability: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(64))
    strategy: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[str | None] = mapped_column(String(32))
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class TrajectoryRecord(Base):
    __tablename__ = "trajectories"

    # hash(run_id, task_type, field_type, workflow_version)
    trajectory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"), index=True)
    task_type: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(64))
    workflow_version: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class DistillationJobRecord(Base):
    __tablename__ = "distillation_jobs"
    __table_args__ = (Index("ix_distillation_jobs_status", "status"),)

    # hash(trajectory_id, distiller_version): the idempotency key for a distillation.
    distillation_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    trajectory_id: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.run_id"), index=True)
    task_type: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(64))
    workflow_version: Mapped[int] = mapped_column(Integer)
    distiller_version: Mapped[str] = mapped_column(String(64))
    # PENDING | RUNNING | SUCCEEDED | INELIGIBLE | REJECTED | FAILED
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    result_procedure_version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ResearchJobRecord(Base):
    """A queued city run. The queue row is the source of truth for run status."""

    __tablename__ = "research_jobs"
    __table_args__ = (
        Index("ix_research_jobs_claim", "status", "available_at", "priority"),
        # At most one QUEUED/RUNNING job per city: duplicate submissions return it instead.
        Index(
            "uq_research_jobs_active_city", "city_key", unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
            sqlite_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    city_name: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(255))
    city_key: Mapped[str] = mapped_column(String(512))  # normalized city|country
    # QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    resumed: Mapped[bool] = mapped_column(Boolean, default=False)  # last attempt resumed a checkpoint
    last_error: Mapped[str | None] = mapped_column(String(2000))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ResearchRunStepRecord(Base):
    """One completed graph step of one attempt, with a compact output summary (for the UI)."""

    __tablename__ = "research_run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt", "seq", name="uq_research_run_steps_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)  # order within the attempt
    node: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[float] = mapped_column(Float)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
