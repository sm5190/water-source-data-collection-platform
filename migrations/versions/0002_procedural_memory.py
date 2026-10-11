"""Procedural memory: research runs, trusted events, trajectories, distillation jobs,
procedures (logical + immutable versions), provenance and usage.

Postgres-only pieces (pgvector extension, HNSW index) are guarded by dialect so the same
migration also runs on SQLite in tests.

Revision ID: 0002_procedural_memory
Revises: 0001_baseline
Create Date: 2026-10-09 01:00:06.896623
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0002_procedural_memory'
down_revision: str | None = '0001_baseline'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table('procedures',
    sa.Column('procedure_id', sa.String(length=36), nullable=False),
    sa.Column('procedure_key', sa.String(length=64), nullable=False),
    sa.Column('task_type', sa.String(length=64), nullable=False),
    sa.Column('field_type', sa.String(length=64), nullable=False),
    sa.Column('water_type', sa.String(length=32), nullable=True),
    sa.Column('jurisdiction_type', sa.String(length=32), nullable=True),
    sa.Column('language', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('procedure_id'),
    sa.UniqueConstraint('procedure_key')
    )
    with op.batch_alter_table('procedures', schema=None) as batch_op:
        batch_op.create_index('ix_procedures_task_field', ['task_type', 'field_type'], unique=False)

    op.create_table('research_runs',
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('city_name', sa.String(length=255), nullable=False),
    sa.Column('country', sa.String(length=255), nullable=True),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('fields_total', sa.Integer(), nullable=False),
    sa.Column('fields_resolved', sa.Integer(), nullable=False),
    sa.Column('fields_conflicted', sa.Integer(), nullable=False),
    sa.Column('human_review_required', sa.Boolean(), nullable=False),
    sa.Column('human_correction_count', sa.Integer(), nullable=False),
    sa.Column('rejected_claim_count', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('run_id')
    )
    op.create_table('distillation_jobs',
    sa.Column('distillation_key', sa.String(length=64), nullable=False),
    sa.Column('trajectory_id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('task_type', sa.String(length=64), nullable=False),
    sa.Column('field_type', sa.String(length=64), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('distiller_version', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=1000), nullable=True),
    sa.Column('result_procedure_version_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['research_runs.run_id'], ),
    sa.PrimaryKeyConstraint('distillation_key')
    )
    with op.batch_alter_table('distillation_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_distillation_jobs_run_id'), ['run_id'], unique=False)
        batch_op.create_index('ix_distillation_jobs_status', ['status'], unique=False)

    op.create_table('procedure_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('procedure_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('auto_promotable', sa.Boolean(), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('summary', sa.String(length=600), nullable=False),
    sa.Column('recipe_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('recipe_text', sa.String(length=4000), nullable=False),
    sa.Column('recipe_hash', sa.String(length=64), nullable=False),
    sa.Column('required_capabilities', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('min_workflow_version', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('distiller_version', sa.String(length=64), nullable=False),
    sa.Column('embedding', sa.JSON().with_variant(pgvector.sqlalchemy.vector.VECTOR(dim=1536), 'postgresql'), nullable=True),
    sa.Column('embedding_model', sa.String(length=64), nullable=True),
    sa.Column('embedding_version', sa.String(length=16), nullable=True),
    sa.Column('embedding_input_hash', sa.String(length=64), nullable=True),
    sa.Column('embedding_status', sa.String(length=16), nullable=False),
    sa.Column('support_runs', sa.Integer(), nullable=False),
    sa.Column('support_cities', sa.Integer(), nullable=False),
    sa.Column('support_corrected_runs', sa.Integer(), nullable=False),
    sa.Column('retrieved_count', sa.Integer(), nullable=False),
    sa.Column('selected_count', sa.Integer(), nullable=False),
    sa.Column('usage_count', sa.Integer(), nullable=False),
    sa.Column('success_count', sa.Integer(), nullable=False),
    sa.Column('failure_count', sa.Integer(), nullable=False),
    sa.Column('recent_success_rate', sa.Float(), nullable=True),
    sa.Column('human_review_count', sa.Integer(), nullable=False),
    sa.Column('human_correction_count', sa.Integer(), nullable=False),
    sa.Column('sum_tool_calls', sa.Integer(), nullable=False),
    sa.Column('sum_search_rounds', sa.Integer(), nullable=False),
    sa.Column('sum_cost', sa.Float(), nullable=False),
    sa.Column('sum_latency_ms', sa.Float(), nullable=False),
    sa.Column('approved_by', sa.String(length=255), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['procedure_id'], ['procedures.procedure_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('procedure_id', 'version', name='uq_procedure_versions_version')
    )
    with op.batch_alter_table('procedure_versions', schema=None) as batch_op:
        batch_op.create_index('ix_procedure_versions_status', ['status'], unique=False)
        batch_op.create_index('uq_procedure_versions_one_active', ['procedure_id'], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))

    op.create_table('run_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('node', sa.String(length=64), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=True),
    sa.Column('capability', sa.String(length=64), nullable=True),
    sa.Column('provider', sa.String(length=64), nullable=True),
    sa.Column('strategy', sa.String(length=128), nullable=True),
    sa.Column('result', sa.String(length=32), nullable=True),
    sa.Column('attrs', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['research_runs.run_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'seq', name='uq_run_events_run_seq')
    )
    with op.batch_alter_table('run_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_events_run_id'), ['run_id'], unique=False)

    op.create_table('trajectories',
    sa.Column('trajectory_id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('task_type', sa.String(length=64), nullable=False),
    sa.Column('field_type', sa.String(length=64), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['research_runs.run_id'], ),
    sa.PrimaryKeyConstraint('trajectory_id')
    )
    with op.batch_alter_table('trajectories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trajectories_run_id'), ['run_id'], unique=False)

    op.create_table('procedure_origins',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('procedure_version_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('trajectory_id', sa.String(length=64), nullable=False),
    sa.Column('distillation_key', sa.String(length=64), nullable=False),
    sa.Column('city_name', sa.String(length=255), nullable=False),
    sa.Column('contribution_type', sa.String(length=24), nullable=False),
    sa.Column('human_corrected', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['procedure_version_id'], ['procedure_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('distillation_key')
    )
    with op.batch_alter_table('procedure_origins', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_procedure_origins_procedure_version_id'), ['procedure_version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_procedure_origins_run_id'), ['run_id'], unique=False)

    op.create_table('procedure_usages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('procedure_version_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('retrieved_rank', sa.Integer(), nullable=False),
    sa.Column('retrieved_score', sa.Float(), nullable=False),
    sa.Column('similarity', sa.Float(), nullable=True),
    sa.Column('selected', sa.Boolean(), nullable=False),
    sa.Column('applied', sa.Boolean(), nullable=False),
    sa.Column('adapted', sa.Boolean(), nullable=False),
    sa.Column('result', sa.String(length=16), nullable=False),
    sa.Column('field_resolved', sa.Boolean(), nullable=True),
    sa.Column('validation_result', sa.String(length=32), nullable=True),
    sa.Column('tool_calls', sa.Integer(), nullable=True),
    sa.Column('search_rounds', sa.Integer(), nullable=True),
    sa.Column('cost', sa.Float(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('human_review_required', sa.Boolean(), nullable=True),
    sa.Column('human_corrected', sa.Boolean(), nullable=True),
    sa.Column('outcome_recorded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['procedure_version_id'], ['procedure_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('procedure_version_id', 'run_id', 'field_key', name='uq_procedure_usages_version_run_field')
    )
    with op.batch_alter_table('procedure_usages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_procedure_usages_procedure_version_id'), ['procedure_version_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_procedure_usages_run_id'), ['run_id'], unique=False)

    if is_pg:
        # Approximate NN index for semantic ranking; retrieval always filters metadata first.
        op.execute(
            "CREATE INDEX ix_procedure_versions_embedding_hnsw ON procedure_versions "
            "USING hnsw (embedding vector_cosine_ops)"
        )

def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_procedure_versions_embedding_hnsw")
    with op.batch_alter_table('procedure_usages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_procedure_usages_run_id'))
        batch_op.drop_index(batch_op.f('ix_procedure_usages_procedure_version_id'))

    op.drop_table('procedure_usages')
    with op.batch_alter_table('procedure_origins', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_procedure_origins_run_id'))
        batch_op.drop_index(batch_op.f('ix_procedure_origins_procedure_version_id'))

    op.drop_table('procedure_origins')
    with op.batch_alter_table('trajectories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_trajectories_run_id'))

    op.drop_table('trajectories')
    with op.batch_alter_table('run_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_events_run_id'))

    op.drop_table('run_events')
    with op.batch_alter_table('procedure_versions', schema=None) as batch_op:
        batch_op.drop_index('uq_procedure_versions_one_active', postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))
        batch_op.drop_index('ix_procedure_versions_status')

    op.drop_table('procedure_versions')
    with op.batch_alter_table('distillation_jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_distillation_jobs_status')
        batch_op.drop_index(batch_op.f('ix_distillation_jobs_run_id'))

    op.drop_table('distillation_jobs')
    op.drop_table('research_runs')
    with op.batch_alter_table('procedures', schema=None) as batch_op:
        batch_op.drop_index('ix_procedures_task_field')

    op.drop_table('procedures')
