"""Human review: review_items and append-only review_decisions, the AWAITING_REVIEW job status
(one active job per city now includes runs waiting for review), the review round on
research_jobs, and review links on claims and resolution decisions. Additive only.

Revision ID: 0007_human_review
Revises: 0006_claim_resolution
Create Date: 2026-10-19 18:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0007_human_review'
down_revision: str | None = '0006_claim_resolution'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIVE = "status IN ('QUEUED', 'RUNNING')"
NEW_ACTIVE = "status IN ('QUEUED', 'RUNNING', 'AWAITING_REVIEW')"

def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

def _active_index(batch_op, where: str) -> None:
    batch_op.create_index('uq_research_jobs_active_city', ['city_key'], unique=True,
                          postgresql_where=sa.text(where), sqlite_where=sa.text(where))

def upgrade() -> None:
    op.create_table('review_items',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('entity_key', sa.String(length=255), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('reason_codes', _json(), nullable=False),
    sa.Column('candidate_claim_uids', _json(), nullable=False),
    sa.Column('payload', _json(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'round', 'field_key', 'entity_key',
                        name='uq_review_items_round_entity')
    )
    with op.batch_alter_table('review_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_items_run_id'), ['run_id'], unique=False)

    op.create_table('review_decisions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('decision_key', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('item_id', sa.String(length=32), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('entity_key', sa.String(length=255), nullable=False),
    sa.Column('reviewer', sa.String(length=128), nullable=False),
    sa.Column('decision', sa.String(length=24), nullable=False),
    sa.Column('claim_uids', _json(), nullable=False),
    sa.Column('original_value', _json(), nullable=True),
    sa.Column('reviewed_value', _json(), nullable=True),
    sa.Column('value_origin', sa.String(length=16), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('correction_category', sa.String(length=64), nullable=True),
    sa.Column('evidence', _json(), nullable=True),
    sa.Column('target_entity_key', sa.String(length=255), nullable=True),
    sa.Column('status_before', sa.String(length=32), nullable=False),
    sa.Column('supersedes_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['review_items.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('decision_key')
    )
    with op.batch_alter_table('review_decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_decisions_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_decisions_item_id'), ['item_id'],
                              unique=False)

    with op.batch_alter_table('field_claims', schema=None) as batch_op:
        batch_op.add_column(sa.Column('review_status', sa.String(length=16), nullable=True))
    with op.batch_alter_table('field_resolutions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('review_decision_id', sa.Integer(), nullable=True))
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('review_round', sa.Integer(), nullable=False,
                                      server_default='0'))
        batch_op.drop_index('uq_research_jobs_active_city', postgresql_where=sa.text(OLD_ACTIVE),
                            sqlite_where=sa.text(OLD_ACTIVE))
        _active_index(batch_op, NEW_ACTIVE)
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.alter_column('review_round', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_index('uq_research_jobs_active_city', postgresql_where=sa.text(NEW_ACTIVE),
                            sqlite_where=sa.text(NEW_ACTIVE))
        _active_index(batch_op, OLD_ACTIVE)
        batch_op.drop_column('review_round')
    with op.batch_alter_table('field_resolutions', schema=None) as batch_op:
        batch_op.drop_column('review_decision_id')
    with op.batch_alter_table('field_claims', schema=None) as batch_op:
        batch_op.drop_column('review_status')

    with op.batch_alter_table('review_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_decisions_item_id'))
        batch_op.drop_index(batch_op.f('ix_review_decisions_run_id'))
    op.drop_table('review_decisions')
    with op.batch_alter_table('review_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_items_run_id'))
    op.drop_table('review_items')
