"""Reprocessing: a run can be a REPROCESS of an earlier run (its stored claims and evidence are
validated, resolved, reviewed and assembled again without new discovery, acquisition or model
calls). Adds research_jobs.mode and research_jobs.parent_run_id. Additive only.

Revision ID: 0009_reprocess_runs
Revises: 0008_supply_records
Create Date: 2026-10-20 14:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = '0009_reprocess_runs'
down_revision: str | None = '0008_supply_records'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mode', sa.String(length=16), nullable=False,
                                      server_default='RESEARCH'))
        batch_op.add_column(sa.Column('parent_run_id', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_research_jobs_parent_run_id'), ['parent_run_id'],
                              unique=False)
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.alter_column('mode', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_jobs_parent_run_id'))
        batch_op.drop_column('parent_run_id')
        batch_op.drop_column('mode')
