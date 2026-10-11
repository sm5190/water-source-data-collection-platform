"""Baseline: context_bundles (previously created via create_all).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-09
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

def upgrade() -> None:
    op.create_table(
        "context_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("city_name", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("target_fields", JSONType, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("payload", JSONType, nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_context_bundles_city_name", "context_bundles", ["city_name"])
    op.create_index("ix_context_bundles_fingerprint", "context_bundles", ["fingerprint"])

def downgrade() -> None:
    op.drop_table("context_bundles")
