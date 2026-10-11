"""Procedural memory tables: logical procedures, immutable versions, provenance, usage."""

from datetime import datetime
from typing import Any
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column
from water_source_research.core.versions import EMBEDDING_DIMENSIONS
from .models import Base, JSONType, utcnow

# pgvector on Postgres; JSON list elsewhere (tests use SQLite and compute cosine in Python).
EmbeddingType = JSON().with_variant(Vector(EMBEDDING_DIMENSIONS), "postgresql")

class ProcedureRecord(Base):
    """Logical procedure. Its identity is compatibility metadata plus the capability plan."""

    __tablename__ = "procedures"
    __table_args__ = (Index("ix_procedures_task_field", "task_type", "field_type"),)

    procedure_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    procedure_key: Mapped[str] = mapped_column(String(64), unique=True)
    task_type: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(64))
    water_type: Mapped[str | None] = mapped_column(String(32))
    jurisdiction_type: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ProcedureVersionRecord(Base):
    """One immutable recipe. Only status, statistics and embedding columns ever change."""

    __tablename__ = "procedure_versions"
    __table_args__ = (
        UniqueConstraint("procedure_id", "version", name="uq_procedure_versions_version"),
        Index("ix_procedure_versions_status", "status"),
        # At most one ACTIVE version per procedure, enforced by the database.
        Index(
            "uq_procedure_versions_one_active", "procedure_id", unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        # Approximate-NN index for semantic ranking (Postgres/pgvector only).
        Index(
            "ix_procedure_versions_embedding_hnsw", "embedding",
            postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    procedure_id: Mapped[str] = mapped_column(ForeignKey("procedures.procedure_id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    auto_promotable: Mapped[bool] = mapped_column(Boolean, default=True)

    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(String(600))
    recipe_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    recipe_text: Mapped[str] = mapped_column(String(4000))
    recipe_hash: Mapped[str] = mapped_column(String(64))
    required_capabilities: Mapped[list[str]] = mapped_column(JSONType)

    workflow_version: Mapped[int] = mapped_column(Integer)
    min_workflow_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    distiller_version: Mapped[str] = mapped_column(String(64))

    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType)
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_version: Mapped[str | None] = mapped_column(String(16))
    embedding_input_hash: Mapped[str | None] = mapped_column(String(64))
    embedding_status: Mapped[str] = mapped_column(String(16), default="PENDING")

    # Provenance-derived support (recomputed from procedure_origins).
    support_runs: Mapped[int] = mapped_column(Integer, default=0)
    support_cities: Mapped[int] = mapped_column(Integer, default=0)
    support_corrected_runs: Mapped[int] = mapped_column(Integer, default=0)

    # Usage statistics (atomic SQL increments only).
    retrieved_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)  # applied
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    recent_success_rate: Mapped[float | None] = mapped_column(Float)  # EWMA of outcomes
    human_review_count: Mapped[int] = mapped_column(Integer, default=0)
    human_correction_count: Mapped[int] = mapped_column(Integer, default=0)
    sum_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    sum_search_rounds: Mapped[int] = mapped_column(Integer, default=0)
    sum_cost: Mapped[float] = mapped_column(Float, default=0.0)
    sum_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ProcedureOriginRecord(Base):
    """Which runs/trajectories produced or support a version."""

    __tablename__ = "procedure_origins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    procedure_version_id: Mapped[str] = mapped_column(
        ForeignKey("procedure_versions.id"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    trajectory_id: Mapped[str] = mapped_column(String(64))
    # One distillation result per distillation key: retries can never double-count support.
    distillation_key: Mapped[str] = mapped_column(String(64), unique=True)
    city_name: Mapped[str] = mapped_column(String(255))
    contribution_type: Mapped[str] = mapped_column(String(24))  # CREATED | MERGED_SUPPORT
    human_corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ProcedureUsageRecord(Base):
    """retrieved -> selected -> applied -> outcome, per (version, run, field)."""

    __tablename__ = "procedure_usages"
    __table_args__ = (
        UniqueConstraint("procedure_version_id", "run_id", "field_key",
                         name="uq_procedure_usages_version_run_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    procedure_version_id: Mapped[str] = mapped_column(
        ForeignKey("procedure_versions.id"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    field_key: Mapped[str] = mapped_column(String(128))
    retrieved_rank: Mapped[int] = mapped_column(Integer)
    retrieved_score: Mapped[float] = mapped_column(Float)
    similarity: Mapped[float | None] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    adapted: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(String(16), default="PENDING")
    field_resolved: Mapped[bool | None] = mapped_column(Boolean)
    validation_result: Mapped[str | None] = mapped_column(String(32))
    tool_calls: Mapped[int | None] = mapped_column(Integer)
    search_rounds: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    human_review_required: Mapped[bool | None] = mapped_column(Boolean)
    human_corrected: Mapped[bool | None] = mapped_column(Boolean)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
