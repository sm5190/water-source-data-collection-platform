"""Evaluation of research runs against reference data: reference dataset versions and
values, evaluation runs (with the research snapshot they evaluated), field-level results,
evidence checks, failure diagnoses, append-only adjudications and metrics. New eval_* tables
only; research tables are untouched.

Revision ID: 0010_evaluation
Revises: 0009_reprocess_runs
Create Date: 2026-10-19 03:20:09.739190
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0010_evaluation'
down_revision: str | None = '0009_reprocess_runs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('eval_reference_datasets',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('filename', sa.String(length=512), nullable=False),
    sa.Column('file_sha256', sa.String(length=64), nullable=False),
    sa.Column('sheet', sa.String(length=128), nullable=True),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('cities', _json(), nullable=False),
    sa.Column('column_map', _json(), nullable=False),
    sa.Column('issues', _json(), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('eval_reference_values',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('dataset_id', sa.String(length=32), nullable=False),
    sa.Column('city_key', sa.String(length=512), nullable=False),
    sa.Column('scope', sa.String(length=8), nullable=False),
    sa.Column('record_key', sa.String(length=255), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('raw', _json(), nullable=True),
    sa.Column('normalized', _json(), nullable=True),
    sa.Column('value_status', sa.String(length=16), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=True),
    sa.Column('reference_year', sa.Integer(), nullable=True),
    sa.Column('source_urls', _json(), nullable=False),
    sa.Column('issues', _json(), nullable=False),
    sa.Column('row', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['dataset_id'], ['eval_reference_datasets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dataset_id', 'city_key', 'scope', 'record_key', 'field_key', name='uq_eval_reference_values_cell')
    )
    with op.batch_alter_table('eval_reference_values', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_reference_values_dataset_id'), ['dataset_id'], unique=False)

    op.create_table('eval_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('research_run_id', sa.String(length=64), nullable=False),
    sa.Column('origin_run_id', sa.String(length=64), nullable=False),
    sa.Column('dataset_id', sa.String(length=32), nullable=False),
    sa.Column('city_key', sa.String(length=512), nullable=False),
    sa.Column('compare_policy_version', sa.String(length=32), nullable=False),
    sa.Column('config', _json(), nullable=False),
    sa.Column('snapshot_sha256', sa.String(length=64), nullable=False),
    sa.Column('snapshot', _json(), nullable=False),
    sa.Column('stages', _json(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('gaps', _json(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['dataset_id'], ['eval_reference_datasets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_runs_research_run_id'), ['research_run_id'], unique=False)

    op.create_table('eval_adjudications',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('eval_run_id', sa.String(length=36), nullable=False),
    sa.Column('target', sa.String(length=16), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('decision', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=True),
    sa.Column('root_cause', sa.String(length=40), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('reviewer', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_adjudications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_adjudications_eval_run_id'), ['eval_run_id'], unique=False)

    op.create_table('eval_diagnoses',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('eval_run_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=8), nullable=False),
    sa.Column('record_key', sa.String(length=255), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('root_cause', sa.String(length=40), nullable=False),
    sa.Column('earliest_stage', sa.String(length=24), nullable=True),
    sa.Column('confidence', sa.String(length=16), nullable=False),
    sa.Column('symptoms', _json(), nullable=False),
    sa.Column('probes', _json(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_diagnoses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_diagnoses_eval_run_id'), ['eval_run_id'], unique=False)

    op.create_table('eval_evidence_checks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('eval_run_id', sa.String(length=36), nullable=False),
    sa.Column('claim_uid', sa.String(length=32), nullable=True),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('citation_index', sa.Integer(), nullable=True),
    sa.Column('check', sa.String(length=40), nullable=False),
    sa.Column('result', sa.String(length=16), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('links', _json(), nullable=False),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_evidence_checks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_evidence_checks_eval_run_id'), ['eval_run_id'], unique=False)

    op.create_table('eval_field_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('eval_run_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=8), nullable=False),
    sa.Column('scope', sa.String(length=8), nullable=False),
    sa.Column('record_key', sa.String(length=255), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('reference', _json(), nullable=False),
    sa.Column('predicted', _json(), nullable=False),
    sa.Column('comparison', _json(), nullable=False),
    sa.Column('links', _json(), nullable=False),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('eval_run_id', 'stage', 'record_key', 'field_key', name='uq_eval_field_results_cell')
    )
    with op.batch_alter_table('eval_field_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_field_results_eval_run_id'), ['eval_run_id'], unique=False)

    op.create_table('eval_metrics',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('eval_run_id', sa.String(length=36), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('scope_key', sa.String(length=128), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('numerator', sa.Float(), nullable=True),
    sa.Column('denominator', sa.Float(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('detail', _json(), nullable=False),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_metrics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_metrics_eval_run_id'), ['eval_run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('eval_metrics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_metrics_eval_run_id'))

    op.drop_table('eval_metrics')
    with op.batch_alter_table('eval_field_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_field_results_eval_run_id'))

    op.drop_table('eval_field_results')
    with op.batch_alter_table('eval_evidence_checks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_evidence_checks_eval_run_id'))

    op.drop_table('eval_evidence_checks')
    with op.batch_alter_table('eval_diagnoses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_diagnoses_eval_run_id'))

    op.drop_table('eval_diagnoses')
    with op.batch_alter_table('eval_adjudications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_adjudications_eval_run_id'))

    op.drop_table('eval_adjudications')
    with op.batch_alter_table('eval_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_runs_research_run_id'))

    op.drop_table('eval_runs')
    with op.batch_alter_table('eval_reference_values', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_reference_values_dataset_id'))

    op.drop_table('eval_reference_values')
    op.drop_table('eval_reference_datasets')
