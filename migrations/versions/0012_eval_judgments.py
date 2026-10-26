"""Semantic judgments of manual vs automated values (cache and audit for the evaluation's LLM
judge). New eval_judgments table only.

Revision ID: 0012_eval_judgments
Revises: 0011_review_audit
Create Date: 2026-10-25 16:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0012_eval_judgments'
down_revision: str | None = '0011_review_audit'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('eval_judgments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('cache_key', sa.String(length=64), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('inputs', _json(), nullable=False),
    sa.Column('output', _json(), nullable=False),
    sa.Column('verdict', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cache_key')
    )
    with op.batch_alter_table('eval_judgments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_judgments_field_key'), ['field_key'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('eval_judgments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_judgments_field_key'))
    op.drop_table('eval_judgments')
