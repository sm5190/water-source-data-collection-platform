"""Per-run evidence and claims: the provenance chain of a city run in queryable form.

SourceDocument (one attempted URL, raw bytes in the artifact store) -> EvidenceChunk (text
the context manager selects from) -> ClaimCitation (exact quote) -> FieldClaim ->
FieldResolution (append-only decisions) -> RunFieldState (current snapshot).
Written by the graph (acquire: documents/chunks; finalize: field states, claims, citations and
resolution decisions). Claims are keyed by their stable claim_uid and never deleted.
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
    # Who published it, under which classification rules (NULL = classify-1, before both),
    # and the signals that decided it; a copy keeps its original classification there.
    publisher_kind: Mapped[str | None] = mapped_column(String(32))
    classification_version: Mapped[str | None] = mapped_column(String(32))
    classification_signals: Mapped[dict[str, Any] | None] = mapped_column(JSONType,
                                                                         nullable=True)
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
    # Serialized FieldResolution: per-entity status, rule, reasons and selected claim uids.
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class FieldClaimRecord(Base):
    __tablename__ = "field_claims"
    # Rows written before claim_uid existed keep NULL (unconstrained).
    __table_args__ = (UniqueConstraint("run_id", "claim_uid", name="uq_field_claims_run_uid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    field_key: Mapped[str] = mapped_column(String(128))
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    value_status: Mapped[str] = mapped_column(String(32))
    value_origin: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    claim_uid: Mapped[str | None] = mapped_column(String(32))
    entity_key: Mapped[str | None] = mapped_column(String(255))
    original_value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    normalized_value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    qualifiers: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    validation_outcome: Mapped[str | None] = mapped_column(String(24))
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(16))  # REJECTED | CORRECTED
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

class FieldResolutionRecord(Base):
    """Append-only log of resolution decisions per (run, field, entity); the latest row per
    (run, field, entity) is current. Rules write them now; reviewer decisions will too."""

    __tablename__ = "field_resolutions"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_field_resolutions_run_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # order within the run
    field_key: Mapped[str] = mapped_column(String(128))
    entity_key: Mapped[str] = mapped_column(String(255))  # city | * | <source key> | unattributed
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    rule: Mapped[str] = mapped_column(String(64))
    reason_codes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    selected_claim_uids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    candidate_claim_uids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    decided_by: Mapped[str] = mapped_column(String(16))  # RULES | REVIEWER
    policy_version: Mapped[str] = mapped_column(String(32))
    review_decision_id: Mapped[int | None] = mapped_column(Integer)  # REVIEWER rows
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RunUtilityCandidateRecord(Base):
    """A utility candidate of a run's utility registry (services/utility_registry.py) as of the
    start of a discovery round (``round`` = rounds + 1: after the last round, before
    validation). Discovery provenance only; never a claim decision."""

    __tablename__ = "run_utility_candidates"
    __table_args__ = (UniqueConstraint("run_id", "round", "candidate_key",
                                       name="uq_run_utility_candidates"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    round: Mapped[int] = mapped_column(Integer)
    candidate_key: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32))
    status_reason: Mapped[str | None] = mapped_column(String(1000))
    verified_roles: Mapped[list[str]] = mapped_column(JSONType, default=list)
    aliases: Mapped[list[str]] = mapped_column(JSONType, default=list)
    bound_hosts: Mapped[list[str]] = mapped_column(JSONType, default=list)
    predecessor_of: Mapped[list[str]] = mapped_column(JSONType, default=list)
    successor_of: Mapped[list[str]] = mapped_column(JSONType, default=list)
    origins: Mapped[list[str]] = mapped_column(JSONType, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    policy_version: Mapped[str] = mapped_column(String(32))
    identity: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class DocumentLinkRecord(Base):
    """An outbound link of an acquired HTML page (acquisition/links.py): target, anchor,
    context, nearest heading, footer flag. ``basis`` PARSED (at acquisition) or REPARSED
    (re-read from the stored raw HTML for evaluation). Context for municipal affiliation;
    never evidence by itself."""

    __tablename__ = "document_links"
    __table_args__ = (UniqueConstraint("run_id", "document_id", "link_key",
                                       name="uq_document_links"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    link_key: Mapped[str] = mapped_column(String(40))  # sha1(target_url | anchor_text)
    target_url: Mapped[str] = mapped_column(String(2048))
    target_host: Mapped[str] = mapped_column(String(255))
    anchor_text: Mapped[str] = mapped_column(String(500), default="")
    context: Mapped[str] = mapped_column(String(300), default="")
    heading: Mapped[str] = mapped_column(String(300), default="")
    in_footer: Mapped[bool] = mapped_column(Boolean, default=False)
    basis: Mapped[str] = mapped_column(String(16), default="PARSED")
    observed_at: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RunSupplyRelationshipRecord(Base):
    """The run's water-supply relationship graph (services/supply_relationships.py), one row
    per relationship (status REJECTED rows: statements kept to audit exclusions). A rebuild for
    the same policy version replaces the run's rows; other versions are kept. Provenance only:
    no claim or review decision reads it."""

    __tablename__ = "run_supply_relationships"
    __table_args__ = (UniqueConstraint("run_id", "policy_version", "relationship_key",
                                       name="uq_run_supply_relationships"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_key: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(500))
    object_id: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16))
    temporal: Mapped[str | None] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(32))
    identity: Mapped[str] = mapped_column(String(32))
    geo_basis: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class DiscoveryResultRecord(Base):
    """One search hit as a provider returned it (before merging and ranking), so a later
    diagnosis can tell which URLs the recorded searches returned. Proves only what the
    recorded searches returned, never that a source was unavailable."""

    __tablename__ = "discovery_results"
    __table_args__ = (UniqueConstraint("run_id", "round", "call_index", "result_index",
                                       name="uq_discovery_results_hit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    round: Mapped[int] = mapped_column(Integer)
    call_index: Mapped[int] = mapped_column(Integer)  # provider call within the round
    result_index: Mapped[int] = mapped_column(Integer)  # position in the provider's answer
    provider: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(64))  # web_search | scholarly_search | ...
    query: Mapped[str | None] = mapped_column(String(1000))
    field_key: Mapped[str | None] = mapped_column(String(128))
    rank: Mapped[int | None] = mapped_column(Integer)  # the provider's own rank, if given
    url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str | None] = mapped_column(String(2048), index=True)
    title: Mapped[str | None] = mapped_column(String(1000))
    source_type: Mapped[str | None] = mapped_column(String(32))
    publisher_kind: Mapped[str | None] = mapped_column(String(32))
    classification_version: Mapped[str | None] = mapped_column(String(32))
    doi: Mapped[str | None] = mapped_column(String(255))
    retained: Mapped[bool] = mapped_column(Boolean, default=False)  # among the round's candidates
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
