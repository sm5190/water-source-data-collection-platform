"""Human review of a run: what was asked (review_items) and what was decided (review_decisions).

Items are created by the graph's human_review node, one per (round, field, entity) the rules
could not settle, with a snapshot of the candidates and evidence shown to the reviewer. Decisions
are append-only (never updated or deleted): a later decision for the same item supersedes an
earlier one, and the applied result is recorded in field_resolutions (decided_by REVIEWER).
"""

from datetime import datetime
from typing import Any
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, JSONType, utcnow

class ReviewItemRecord(Base):
    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("run_id", "round", "field_key", "entity_key",
                                       name="uq_review_items_round_entity"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # hash(run, round, field, entity)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    round: Mapped[int] = mapped_column(Integer)
    field_key: Mapped[str] = mapped_column(String(128))
    entity_key: Mapped[str] = mapped_column(String(255))
    label: Mapped[str | None] = mapped_column(String(255))
    reason_codes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    candidate_claim_uids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)  # what the reviewer was shown
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN | APPLIED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class ReviewDecisionRecord(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_key: Mapped[str] = mapped_column(String(64), unique=True)  # client idempotency key
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    field_key: Mapped[str] = mapped_column(String(128))
    entity_key: Mapped[str] = mapped_column(String(255))
    reviewer: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(24))
    claim_uids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    original_value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    reviewed_value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    value_origin: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    correction_category: Mapped[str | None] = mapped_column(String(64))
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    target_entity_key: Mapped[str | None] = mapped_column(String(255))  # ASSIGN_SOURCE
    status_before: Mapped[str] = mapped_column(String(32))
    supersedes_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
