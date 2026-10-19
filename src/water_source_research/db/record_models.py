"""Assembled SupplyRecords of a run, their values, and the exports generated from them.

supply_records/record_values are a snapshot rebuilt at finalize from the run's (immutable)
claims and its resolution decisions; every value names its claims or its calculation, so an
exported cell traces back: record_values -> field_claims -> claim_citations -> evidence_chunks
-> source_documents. research_exports is the audit trail of generated files.
"""

from datetime import datetime
from typing import Any
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, JSONType, utcnow

class SupplyRecordRecord(Base):
    __tablename__ = "supply_records"
    __table_args__ = (UniqueConstraint("run_id", "record_key", name="uq_supply_records_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    record_key: Mapped[str] = mapped_column(String(32))  # hash(city, country, source key)
    source_key: Mapped[str] = mapped_column(String(255), index=True)  # shared across cities
    water_source: Mapped[str | None] = mapped_column(String(255))
    utility_name: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    claim_uids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RecordValueRecord(Base):
    __tablename__ = "record_values"
    __table_args__ = (UniqueConstraint("run_id", "scope", "record_key", "field_key",
                                       name="uq_record_values_cell"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(8))  # CITY (stored once) | RECORD
    record_key: Mapped[str] = mapped_column(String(32))  # "" for CITY values
    field_key: Mapped[str] = mapped_column(String(128))
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    value_status: Mapped[str] = mapped_column(String(32))
    value_origin: Mapped[str | None] = mapped_column(String(32))
    resolution_status: Mapped[str] = mapped_column(String(32))
    reference_year: Mapped[int | None] = mapped_column(Integer)
    claim_uids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    review_decision_ids: Mapped[list[int]] = mapped_column(JSONType, default=list)
    calculation: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    note: Mapped[str | None] = mapped_column(Text)

class ResearchExportRecord(Base):
    __tablename__ = "research_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # standardized | provenance
    format: Mapped[str] = mapped_column(String(8))  # csv | xlsx
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    completion: Mapped[str | None] = mapped_column(String(32))
    requested_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
