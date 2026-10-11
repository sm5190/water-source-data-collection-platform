"""Per-step progress records for city runs (research_run_steps), shown in the UI.

Revision ID: 0004_research_run_steps
Revises: 0003_research_jobs
Create Date: 2026-10-09 02:06:58.275725
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0004_research_run_steps'
down_revision: str | None = '0003_research_jobs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('research_run_steps',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('node', sa.String(length=64), nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=False),
    sa.Column('summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'attempt', 'seq', name='uq_research_run_steps_seq')
    )
    with op.batch_alter_table('research_run_steps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_run_steps_run_id'), ['run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('research_run_steps', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_run_steps_run_id'))

    op.drop_table('research_run_steps')
