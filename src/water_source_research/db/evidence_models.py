"""Per-run evidence and claims: the provenance chain of a city run in queryable form.

SourceDocument (one attempted URL, raw bytes in the artifact store) -> EvidenceChunk (text
the context manager selects from) -> ClaimCitation (exact quote) -> FieldClaim -> RunFieldState.
Written by the graph (acquire: documents/chunks; finalize: field states/claims/citations).
"""

from datetime import datetime
from typing import Any
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, JSONType, utcnow

class SourceDocumentRecord(Base):
    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("run_id", "canonical_url",
                                       name="uq_source_documents_run_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    round: Mapped[int] = mapped_column(Integer, default=1)
    requested_url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    final_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(1000))
    source_type: Mapped[str | None] = mapped_column(String(32))
    acquisition_method: Mapped[str | None] = mapped_column(String(32))
    media_type: Mapped[str | None] = mapped_column(String(255))
    retrieved_at: Mapped[str | None] = mapped_column(String(40))
    source_date: Mapped[str | None] = mapped_column(String(40))
    content_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # OK | EMPTY | FAILED | SKIPPED_DUPLICATE
    status: Mapped[str] = mapped_column(String(32))
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    raw_object_key: Mapped[str | None] = mapped_column(String(512))
    artifact_error: Mapped[str | None] = mapped_column(String(500))
    discovery_provider: Mapped[str | None] = mapped_column(String(64))
    discovery_query: Mapped[str | None] = mapped_column(String(1000))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class EvidenceChunkRecord(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (UniqueConstraint("run_id", "chunk_id", name="uq_evidence_chunks_run_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    chunk_id: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    page_number: Mapped[int | None] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)  # position within the document
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RunFieldStateRecord(Base):
    __tablename__ = "run_field_states"
    __table_args__ = (UniqueConstraint("run_id", "field_key", name="uq_run_field_states"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    field_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    review_required: Mapped[bool] = mapped_column(Boolean)
    needs_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_claim_id: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class FieldClaimRecord(Base):
    __tablename__ = "field_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    field_key: Mapped[str] = mapped_column(String(128))
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    value_status: Mapped[str] = mapped_column(String(32))
    value_origin: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ClaimCitationRecord(Base):
    __tablename__ = "claim_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("field_claims.id", ondelete="CASCADE"),
                                          index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(1000))
    page_number: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[str | None] = mapped_column(String(40))
