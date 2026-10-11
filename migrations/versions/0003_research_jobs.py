"""City-run queue: research_jobs (leases, retries, results).

The partial unique index allows one QUEUED/RUNNING job per normalized city, which makes
submission idempotent. LangGraph checkpoint tables are created by the checkpointer's own
setup(), not by Alembic.

Revision ID: 0003_research_jobs
Revises: 0002_procedural_memory
Create Date: 2026-10-09 01:46:20.050234
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0003_research_jobs'
down_revision: str | None = '0002_procedural_memory'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('research_jobs',
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('city_name', sa.String(length=255), nullable=False),
    sa.Column('country', sa.String(length=255), nullable=True),
    sa.Column('city_key', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lease_owner', sa.String(length=128), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('resumed', sa.Boolean(), nullable=False),
    sa.Column('last_error', sa.String(length=2000), nullable=True),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('run_id')
    )
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.create_index('ix_research_jobs_claim', ['status', 'available_at', 'priority'], unique=False)
        batch_op.create_index('uq_research_jobs_active_city', ['city_key'], unique=True, postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"), sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING')"))


def downgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_index('uq_research_jobs_active_city', postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"), sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING')"))
        batch_op.drop_index('ix_research_jobs_claim')

    op.drop_table('research_jobs')
