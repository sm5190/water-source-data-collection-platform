"""Per-run evidence and claims (source_documents, evidence_chunks, run_field_states,
field_claims, claim_citations) and an optional field subset on research_jobs.

Revision ID: 0005_evidence_persistence
Revises: 0004_research_run_steps
Create Date: 2026-10-11 12:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision: str = '0005_evidence_persistence'
down_revision: str | None = '0004_research_run_steps'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _json():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')

def upgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('field_keys', _json(), nullable=True))

    op.create_table('source_documents',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('requested_url', sa.String(length=2048), nullable=False),
    sa.Column('canonical_url', sa.String(length=2048), nullable=False),
    sa.Column('final_url', sa.String(length=2048), nullable=True),
    sa.Column('title', sa.String(length=1000), nullable=True),
    sa.Column('source_type', sa.String(length=32), nullable=True),
    sa.Column('acquisition_method', sa.String(length=32), nullable=True),
    sa.Column('media_type', sa.String(length=255), nullable=True),
    sa.Column('retrieved_at', sa.String(length=40), nullable=True),
    sa.Column('source_date', sa.String(length=40), nullable=True),
    sa.Column('content_sha256', sa.String(length=64), nullable=True),
    sa.Column('byte_size', sa.Integer(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('error_kind', sa.String(length=32), nullable=True),
    sa.Column('error_message', sa.String(length=1000), nullable=True),
    sa.Column('raw_object_key', sa.String(length=512), nullable=True),
    sa.Column('artifact_error', sa.String(length=500), nullable=True),
    sa.Column('discovery_provider', sa.String(length=64), nullable=True),
    sa.Column('discovery_query', sa.String(length=1000), nullable=True),
    sa.Column('duration_ms', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'canonical_url', name='uq_source_documents_run_url')
    )
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_source_documents_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_documents_content_sha256'),
                              ['content_sha256'], unique=False)

    op.create_table('evidence_chunks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('chunk_id', sa.String(length=32), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['source_documents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'chunk_id', name='uq_evidence_chunks_run_chunk')
    )
    with op.batch_alter_table('evidence_chunks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evidence_chunks_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_chunks_document_id'), ['document_id'],
                              unique=False)

    op.create_table('run_field_states',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('review_required', sa.Boolean(), nullable=False),
    sa.Column('needs_evidence', sa.Boolean(), nullable=False),
    sa.Column('selected_claim_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'field_key', name='uq_run_field_states')
    )
    with op.batch_alter_table('run_field_states', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_field_states_run_id'), ['run_id'], unique=False)

    op.create_table('field_claims',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('field_key', sa.String(length=128), nullable=False),
    sa.Column('value', _json(), nullable=True),
    sa.Column('value_status', sa.String(length=32), nullable=False),
    sa.Column('value_origin', sa.String(length=32), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('is_selected', sa.Boolean(), nullable=False),
    sa.Column('prompt_version', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('field_claims', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_field_claims_run_id'), ['run_id'], unique=False)

    op.create_table('claim_citations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=True),
    sa.Column('chunk_id', sa.String(length=32), nullable=True),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('title', sa.String(length=1000), nullable=True),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('retrieved_at', sa.String(length=40), nullable=True),
    sa.ForeignKeyConstraint(['claim_id'], ['field_claims.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('claim_citations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_claim_citations_claim_id'), ['claim_id'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_claim_citations_run_id'), ['run_id'], unique=False)


def downgrade() -> None:
    for table, indexes in (
        ('claim_citations', ('ix_claim_citations_run_id', 'ix_claim_citations_claim_id')),
        ('field_claims', ('ix_field_claims_run_id',)),
        ('run_field_states', ('ix_run_field_states_run_id',)),
        ('evidence_chunks', ('ix_evidence_chunks_document_id', 'ix_evidence_chunks_run_id')),
        ('source_documents', ('ix_source_documents_content_sha256',
                              'ix_source_documents_run_id')),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name in indexes:
                batch_op.drop_index(batch_op.f(name))
        op.drop_table(table)

    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_column('field_keys')
