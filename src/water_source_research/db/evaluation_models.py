"""Evaluation of research runs against reference data: kept apart from research data.

Nothing here is written by, or writes to, the research pipeline. An evaluation run is
immutable once completed: re-evaluating a research run (new reference version or new
comparison rules) creates a new eval_run; adjudications are append-only.
"""

from datetime import datetime
from typing import Any
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, JSONType, utcnow

class EvalReferenceDatasetRecord(Base):
    __tablename__ = "eval_reference_datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # version hash
    name: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(512))
    file_sha256: Mapped[str] = mapped_column(String(64))
    sheet: Mapped[str | None] = mapped_column(String(128))
    policy_version: Mapped[str] = mapped_column(String(32))
    cities: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)  # + records
    column_map: Mapped[dict[str, str]] = mapped_column(JSONType, default=dict)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class EvalReferenceValueRecord(Base):
    __tablename__ = "eval_reference_values"
    __table_args__ = (UniqueConstraint("dataset_id", "city_key", "scope", "record_key",
                                       "field_key", name="uq_eval_reference_values_cell"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("eval_reference_datasets.id"),
                                            index=True)
    city_key: Mapped[str] = mapped_column(String(512))
    scope: Mapped[str] = mapped_column(String(8))  # CITY | RECORD
    record_key: Mapped[str] = mapped_column(String(255))  # "" for CITY
    field_key: Mapped[str] = mapped_column(String(128))
    raw: Mapped[Any] = mapped_column(JSONType, nullable=True)
    normalized: Mapped[Any] = mapped_column(JSONType, nullable=True)
    value_status: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(32))
    reference_year: Mapped[int | None] = mapped_column(Integer)
    source_urls: Mapped[list[str]] = mapped_column(JSONType, default=list)
    issues: Mapped[list[str]] = mapped_column(JSONType, default=list)
    row: Mapped[int | None] = mapped_column(Integer)

class EvalRunRecord(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(String(64), index=True)
    origin_run_id: Mapped[str] = mapped_column(String(64))  # where discovery/extraction ran
    dataset_id: Mapped[str] = mapped_column(ForeignKey("eval_reference_datasets.id"))
    city_key: Mapped[str] = mapped_column(String(512))
    compare_policy_version: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType)
    stages: Mapped[list[str]] = mapped_column(JSONType, default=list)  # RAW, AUTO, FINAL
    status: Mapped[str] = mapped_column(String(16))  # RUNNING | COMPLETED | FAILED
    error: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)  # telemetry
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class EvalFieldResultRecord(Base):
    __tablename__ = "eval_field_results"
    __table_args__ = (UniqueConstraint("eval_run_id", "stage", "record_key", "field_key",
                                       name="uq_eval_field_results_cell"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(8))  # RAW | AUTO | FINAL
    scope: Mapped[str] = mapped_column(String(8))
    record_key: Mapped[str] = mapped_column(String(255))
    field_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24))
    reference: Mapped[dict[str, Any]] = mapped_column(JSONType)
    predicted: Mapped[dict[str, Any]] = mapped_column(JSONType)  # value(s), origin, claim uids
    comparison: Mapped[dict[str, Any]] = mapped_column(JSONType)
    links: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

class EvalEvidenceCheckRecord(Base):
    __tablename__ = "eval_evidence_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    claim_uid: Mapped[str | None] = mapped_column(String(32))
    field_key: Mapped[str] = mapped_column(String(128))
    citation_index: Mapped[int | None] = mapped_column(Integer)
    check: Mapped[str] = mapped_column(String(40))
    result: Mapped[str] = mapped_column(String(16))  # PASS | FAIL | WARN | NEEDS_REVIEW | N/A
    detail: Mapped[str | None] = mapped_column(Text)
    links: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

class EvalDiagnosisRecord(Base):
    __tablename__ = "eval_diagnoses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(8))
    record_key: Mapped[str] = mapped_column(String(255))
    field_key: Mapped[str] = mapped_column(String(128))
    root_cause: Mapped[str] = mapped_column(String(40))
    earliest_stage: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[str] = mapped_column(String(16))  # CONFIRMED | PROBABLE | UNKNOWN
    symptoms: Mapped[list[str]] = mapped_column(JSONType, default=list)
    probes: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    explanation: Mapped[str] = mapped_column(Text)

class EvalAdjudicationRecord(Base):
    """A person's judgement on an evaluation result or diagnosis (append-only; separate from
    the research pipeline's review decisions)."""

    __tablename__ = "eval_adjudications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    target: Mapped[str] = mapped_column(String(16))  # FIELD_RESULT | DIAGNOSIS
    target_id: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(24))  # adjudicated comparison status
    root_cause: Mapped[str | None] = mapped_column(String(40))  # corrected diagnosis
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class EvalMetricRecord(Base):
    __tablename__ = "eval_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16))  # run | field | field_type | stage
    scope_key: Mapped[str] = mapped_column(String(128))
    metric: Mapped[str] = mapped_column(String(64))
    numerator: Mapped[float | None] = mapped_column(Float)
    denominator: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # exclusions

class EvalJudgmentRecord(Base):
    """A semantic (LLM) judgment of whether an automated value means the same as the manual
    one, with its exact inputs, model and prompt version. Shared cache: the same inputs under
    the same prompt and model are judged once. Provisional until a person adjudicates the
    evaluation result that uses it."""

    __tablename__ = "eval_judgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    field_key: Mapped[str] = mapped_column(String(128), index=True)
    prompt_version: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONType)
    output: Mapped[dict[str, Any]] = mapped_column(JSONType)
    verdict: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))  # comparison status after the caps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
