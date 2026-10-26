"""Worker lifecycle and crash records: worker_instances (one row per worker start, latest
heartbeat/sample) and worker_events (append-only, stable event_id for idempotent re-sync
from the supervisor's local event files). New tables only.

Revision ID: 0014_worker_events
Revises: 0013_discovery_results
Create Date: 2026-10-26 01:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0014_worker_events'
down_revision: str | None = '0013_discovery_results'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('worker_instances',
    sa.Column('instance_id', sa.String(length=128), nullable=False),
    sa.Column('supervisor_id', sa.String(length=64), nullable=True),
    sa.Column('host', sa.String(length=255), nullable=True),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sample', JSON, nullable=True),
    sa.Column('active_run_ids', JSON, nullable=True),
    sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('exit_code', sa.Integer(), nullable=True),
    sa.Column('termination_class', sa.String(length=48), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('instance_id')
    )
    with op.batch_alter_table('worker_instances', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_worker_instances_supervisor_id'),
                              ['supervisor_id'], unique=False)
    op.create_table('worker_events',
    sa.Column('event_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('instance_id', sa.String(length=128), nullable=True),
    sa.Column('supervisor_id', sa.String(length=64), nullable=True),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('run_id', sa.String(length=64), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=True),
    sa.Column('trace_id', sa.String(length=32), nullable=True),
    sa.Column('span_id', sa.String(length=16), nullable=True),
    sa.Column('facts', JSON, nullable=False),
    sa.Column('inferred_cause', sa.String(length=48), nullable=True),
    sa.Column('cause_confidence', sa.String(length=16), nullable=True),
    sa.Column('rationale', sa.String(length=2000), nullable=True),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id')
    )
    with op.batch_alter_table('worker_events', schema=None) as batch_op:
        batch_op.create_index('ix_worker_events_kind_time', ['kind', 'occurred_at'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_worker_events_instance_id'), ['instance_id'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_worker_events_run_id'), ['run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('worker_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_worker_events_run_id'))
        batch_op.drop_index(batch_op.f('ix_worker_events_instance_id'))
        batch_op.drop_index('ix_worker_events_kind_time')
    op.drop_table('worker_events')
    with op.batch_alter_table('worker_instances', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_worker_instances_supervisor_id'))
    op.drop_table('worker_instances')
