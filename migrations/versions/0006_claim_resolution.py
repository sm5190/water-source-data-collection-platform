"""Claim normalization/validation columns, stable claim ids, the append-only
field_resolutions log, and per-field resolution snapshots. Additive only: existing rows keep
NULL in the new columns and are never rewritten.

Revision ID: 0006_claim_resolution
Revises: 0005_evidence_persistence
Create Date: 2026-10-19 12:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0006_claim_resolution'
down_revision: str | None = '0005_evidence_persistence'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

CLAIM_COLUMNS = ('claim_uid', 'entity_key', 'original_value', 'normalized_value', 'qualifiers',
                 'validation_outcome', 'validation')

def upgrade() -> None:
    with op.batch_alter_table('field_claims', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claim_uid', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('entity_key', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('original_value', _json(), nullable=True))
        batch_op.add_column(sa.Column('normalized_value', _json(), nullable=True))
        batch_op.add_column(sa.Column('qualifiers', _json(), nullable=True))
        batch_op.add_column(sa.Column('validation_outcome', sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column('validation', _json(), nullable=True))
        batch_op.create_unique_constraint('uq_field_claims_run_uid', ['run_id', 'claim_uid'])

    with op.batch_alter_table('run_field_states', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resolution', _json(), nullable=True))

    op.create_table('field_resolutions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('entity_key', sa.String(length=255), nullable=False),
    sa.Column('from_status', sa.String(length=32), nullable=False),
    sa.Column('to_status', sa.String(length=32), nullable=False),
    sa.Column('rule', sa.String(length=64), nullable=False),
    sa.Column('reason_codes', _json(), nullable=False),
    sa.Column('selected_claim_uids', _json(), nullable=False),
    sa.Column('candidate_claim_uids', _json(), nullable=False),
    sa.Column('decided_by', sa.String(length=16), nullable=False),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'seq', name='uq_field_resolutions_run_seq')
    )
    with op.batch_alter_table('field_resolutions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_field_resolutions_run_id'), ['run_id'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('field_resolutions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_field_resolutions_run_id'))
    op.drop_table('field_resolutions')

    with op.batch_alter_table('run_field_states', schema=None) as batch_op:
        batch_op.drop_column('resolution')

    with op.batch_alter_table('field_claims', schema=None) as batch_op:
        batch_op.drop_constraint('uq_field_claims_run_uid', type_='unique')
        for column in reversed(CLAIM_COLUMNS):
            batch_op.drop_column(column)
