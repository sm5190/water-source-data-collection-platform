from datetime import UTC, datetime
from typing import Any
from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")

def utcnow() -> datetime:
    return datetime.now(UTC)

class Base(DeclarativeBase):
    pass

class ContextBundleRecord(Base):
    """A built ContextBundle, reusable by any worker that computes the same cache key."""

    __tablename__ = "context_bundles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # The unique constraint is what makes concurrent creation safe across processes.
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    city_name: Mapped[str] = mapped_column(String(255), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    target_fields: Mapped[list[str]] = mapped_column(JSONType)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    token_estimate: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

def include_object_for(dialect_name: str):
    """Alembic filter: ignore schema objects declared for another dialect via ``ddl_if``
    (e.g. the Postgres-only HNSW index), so autogenerate/check is clean on every backend."""
    def include_object(obj, name, type_, reflected, compare_to):
        ddl_if = getattr(obj, "_ddl_if", None)
        return not (ddl_if is not None and ddl_if.dialect and ddl_if.dialect != dialect_name)
    return include_object
