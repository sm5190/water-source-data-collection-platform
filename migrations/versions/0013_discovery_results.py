"""Raw discovery results: every search hit a provider returned (query, provider, position,
URL, classified source type, whether it was retained), per run and round. New table only.

Revision ID: 0013_discovery_results
Revises: 0012_eval_judgments
Create Date: 2026-10-25 20:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = '0013_discovery_results'
down_revision: str | None = '0012_eval_judgments'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('discovery_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('call_index', sa.Integer(), nullable=False),
    sa.Column('result_index', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('strategy', sa.String(length=64), nullable=False),
    sa.Column('query', sa.String(length=1000), nullable=True),
    sa.Column('field_key', sa.String(length=128), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('canonical_url', sa.String(length=2048), nullable=True),
    sa.Column('title', sa.String(length=1000), nullable=True),
    sa.Column('source_type', sa.String(length=32), nullable=True),
    sa.Column('doi', sa.String(length=255), nullable=True),
    sa.Column('retained', sa.Boolean(), nullable=False),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'round', 'call_index', 'result_index',
                        name='uq_discovery_results_hit')
    )
    with op.batch_alter_table('discovery_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_discovery_results_run_id'), ['run_id'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_discovery_results_canonical_url'),
                              ['canonical_url'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('discovery_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_discovery_results_canonical_url'))
        batch_op.drop_index(batch_op.f('ix_discovery_results_run_id'))
    op.drop_table('discovery_results')
