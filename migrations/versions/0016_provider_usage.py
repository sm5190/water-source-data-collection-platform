"""provider_usage: the ledger of external API calls (LLM now; Firecrawl and other paid
providers later), one row per logical call, with nullable (= unknown) tokens and cost.
New table only.

Revision ID: 0016_provider_usage
Revises: 0015_run_traces
Create Date: 2026-10-26 08:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0016_provider_usage'
down_revision: str | None = '0015_run_traces'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('provider_usage',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=True),
    sa.Column('category', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('task', sa.String(length=64), nullable=True),
    sa.Column('field_key', sa.String(length=128), nullable=True),
    sa.Column('prompt_version', sa.String(length=64), nullable=True),
    sa.Column('requests', sa.Integer(), nullable=False),
    sa.Column('retries', sa.Integer(), nullable=False),
    sa.Column('cache_hit', sa.Boolean(), nullable=False),
    sa.Column('attempt_errors', JSON, nullable=True),
    sa.Column('error', sa.String(length=32), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cached_tokens', sa.Integer(), nullable=True),
    sa.Column('credits', sa.Float(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('cost_basis', sa.String(length=128), nullable=True),
    sa.Column('trace_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('provider_usage', schema=None) as batch_op:
        batch_op.create_index('ix_provider_usage_created', ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_provider_usage_run_id'), ['run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('provider_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_usage_run_id'))
        batch_op.drop_index('ix_provider_usage_created')
    op.drop_table('provider_usage')
