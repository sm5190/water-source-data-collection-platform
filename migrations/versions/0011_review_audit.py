"""Simple review: review_decisions gains a normalized reviewer key, a plain decision basis,
the claims rejected in favour of another, deliberate validation overrides, and the field status
each applied decision led to. Additive only; existing decisions are untouched (NULLs).

Revision ID: 0011_review_audit
Revises: 0010_evaluation
Create Date: 2026-10-25 12:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0011_review_audit'
down_revision: str | None = '0010_evaluation'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

def upgrade() -> None:
    with op.batch_alter_table('review_decisions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reviewer_key', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('decision_basis', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('rejected_claim_uids', _json(), nullable=True))
        batch_op.add_column(sa.Column('validation_override', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('status_after', sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f('ix_review_decisions_reviewer_key'), ['reviewer_key'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('review_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_decisions_reviewer_key'))
        batch_op.drop_column('status_after')
        batch_op.drop_column('validation_override')
        batch_op.drop_column('rejected_claim_uids')
        batch_op.drop_column('decision_basis')
        batch_op.drop_column('reviewer_key')
