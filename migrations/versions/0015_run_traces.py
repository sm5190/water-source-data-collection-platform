"""Tracing links: research_jobs.trace_requested / trace_context (the W3C context a worker
continues; set only by our API/CLI) and run_traces (run -> trace/span ids per attempt,
recovery, review resume and evaluation). Additive only.

Revision ID: 0015_run_traces
Revises: 0014_worker_events
Create Date: 2026-10-26 03:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0015_run_traces'
down_revision: str | None = '0014_worker_events'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trace_requested', sa.Boolean(),
                                      server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('trace_context', JSON, nullable=True))
    op.create_table('run_traces',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=True),
    sa.Column('trace_id', sa.String(length=32), nullable=False),
    sa.Column('span_id', sa.String(length=16), nullable=False),
    sa.Column('sampled', sa.Boolean(), nullable=False),
    sa.Column('instance_id', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('run_traces', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_traces_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_traces_trace_id'), ['trace_id'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('run_traces', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_traces_trace_id'))
        batch_op.drop_index(batch_op.f('ix_run_traces_run_id'))
    op.drop_table('run_traces')
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_column('trace_context')
        batch_op.drop_column('trace_requested')
