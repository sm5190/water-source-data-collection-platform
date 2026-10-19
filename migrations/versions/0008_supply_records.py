"""SupplyRecords and their values (city values stored once), the export audit trail, and the
scientific completion status of a run (FINALIZED / FINALIZED_WITH_GAPS / INCOMPLETE).
Additive only.

Revision ID: 0008_supply_records
Revises: 0007_human_review
Create Date: 2026-10-20 10:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0008_supply_records'
down_revision: str | None = '0007_human_review'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('supply_records',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('record_key', sa.String(length=32), nullable=False),
    sa.Column('source_key', sa.String(length=255), nullable=False),
    sa.Column('water_source', sa.String(length=255), nullable=True),
    sa.Column('utility_name', sa.String(length=255), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('claim_uids', _json(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'record_key', name='uq_supply_records_run_key')
    )
    with op.batch_alter_table('supply_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_supply_records_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_supply_records_source_key'), ['source_key'],
                              unique=False)

    op.create_table('record_values',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.String(length=8), nullable=False),
    sa.Column('record_key', sa.String(length=32), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('value', _json(), nullable=True),
    sa.Column('value_status', sa.String(length=32), nullable=False),
    sa.Column('value_origin', sa.String(length=32), nullable=True),
    sa.Column('resolution_status', sa.String(length=32), nullable=False),
    sa.Column('reference_year', sa.Integer(), nullable=True),
    sa.Column('claim_uids', _json(), nullable=False),
    sa.Column('review_decision_ids', _json(), nullable=False),
    sa.Column('calculation', _json(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'scope', 'record_key', 'field_key',
                        name='uq_record_values_cell')
    )
    with op.batch_alter_table('record_values', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_record_values_run_id'), ['run_id'], unique=False)

    op.create_table('research_exports',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('format', sa.String(length=8), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('completion', sa.String(length=32), nullable=True),
    sa.Column('requested_by', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('research_exports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_exports_run_id'), ['run_id'],
                              unique=False)

    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('completion', sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_column('completion')
    for table, indexes in (
        ('research_exports', ('ix_research_exports_run_id',)),
        ('record_values', ('ix_record_values_run_id',)),
        ('supply_records', ('ix_supply_records_source_key', 'ix_supply_records_run_id')),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name in indexes:
                batch_op.drop_index(batch_op.f(name))
        op.drop_table(table)
