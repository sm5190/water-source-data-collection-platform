"""Utility registry snapshots: each run's utility candidates (status, verified roles, own
hosts, aliases, predecessor links and the evidence for each relationship) at the start of each
discovery round and before validation. New table only.

Revision ID: 0018_run_utility_candidates
Revises: 0017_llm_response_cache
Create Date: 2026-11-01 12:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0018_run_utility_candidates'
down_revision: str | None = '0017_llm_response_cache'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('run_utility_candidates',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('candidate_key', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('status_reason', sa.String(length=1000), nullable=True),
    sa.Column('verified_roles', JSON, nullable=False),
    sa.Column('aliases', JSON, nullable=False),
    sa.Column('bound_hosts', JSON, nullable=False),
    sa.Column('predecessor_of', JSON, nullable=False),
    sa.Column('successor_of', JSON, nullable=False),
    sa.Column('origins', JSON, nullable=False),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('identity', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'round', 'candidate_key', name='uq_run_utility_candidates')
    )
    with op.batch_alter_table('run_utility_candidates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_utility_candidates_run_id'), ['run_id'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('run_utility_candidates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_utility_candidates_run_id'))
    op.drop_table('run_utility_candidates')
