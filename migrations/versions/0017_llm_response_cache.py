"""llm_response_cache: persistent application cache of validated model responses, and which
cache answered a ledger row (provider_usage.cache_layer / cache_source). New table and two
nullable columns only.

Revision ID: 0017_llm_response_cache
Revises: 0016_provider_usage
Create Date: 2026-10-29 08:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0017_llm_response_cache'
down_revision: str | None = '0016_provider_usage'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('llm_response_cache',
    sa.Column('cache_key', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('base_url', sa.String(length=512), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('generation_hash', sa.String(length=64), nullable=False),
    sa.Column('task', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=64), nullable=False),
    sa.Column('schema_hash', sa.String(length=64), nullable=False),
    sa.Column('context', JSON, nullable=False),
    sa.Column('prompt_hash', sa.String(length=64), nullable=False),
    sa.Column('response', JSON, nullable=False),
    sa.Column('requests', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cached_tokens', sa.Integer(), nullable=True),
    sa.Column('source_run_id', sa.String(length=64), nullable=True),
    sa.Column('source_trace_id', sa.String(length=32), nullable=True),
    sa.Column('hit_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_hit_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('cache_key')
    )
    with op.batch_alter_table('llm_response_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_llm_response_cache_task'), ['task'], unique=False)
    with op.batch_alter_table('provider_usage', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cache_layer', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('cache_source', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('provider_usage', schema=None) as batch_op:
        batch_op.drop_column('cache_source')
        batch_op.drop_column('cache_layer')
    with op.batch_alter_table('llm_response_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llm_response_cache_task'))
    op.drop_table('llm_response_cache')
