"""Firecrawl fetch fallback: documents keyed by (run, URL, attempt) so a Firecrawl attempt is
kept beside the failed direct fetch (every existing row is 'direct'); the per-run attempt
ledger that makes budget reservations survive restarts; estimated credits in the usage
ledger (actual stays NULL = Unknown). Reversible.

Revision ID: 0023_firecrawl_attempts
Revises: 0022_claim_citation_spans
Create Date: 2026-11-04 01:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0023_firecrawl_attempts'
down_revision: str | None = '0022_claim_citation_spans'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('attempt', sa.String(length=16), nullable=False,
                                      server_default='direct'))
        batch_op.add_column(sa.Column('escalation', JSON, nullable=True))
        batch_op.drop_constraint('uq_source_documents_run_url', type_='unique')
        batch_op.create_unique_constraint('uq_source_documents_run_url_attempt',
                                          ['run_id', 'canonical_url', 'attempt'])
    op.create_table(
        'firecrawl_attempts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(length=64), nullable=False),
        sa.Column('canonical_url', sa.String(length=2048), nullable=False),
        sa.Column('round', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('credits_reserved', sa.Float(), nullable=False),
        sa.Column('credits_estimated', sa.Float(), nullable=True),
        sa.Column('outcome', sa.String(length=32), nullable=True),
        sa.Column('error_kind', sa.String(length=32), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'canonical_url', name='uq_firecrawl_attempts_run_url'),
    )
    with op.batch_alter_table('firecrawl_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_firecrawl_attempts_run_id'), ['run_id'],
                              unique=False)
    with op.batch_alter_table('provider_usage', schema=None) as batch_op:
        batch_op.add_column(sa.Column('credits_estimated', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('provider_usage', schema=None) as batch_op:
        batch_op.drop_column('credits_estimated')
    with op.batch_alter_table('firecrawl_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_firecrawl_attempts_run_id'))
    op.drop_table('firecrawl_attempts')
    # Firecrawl documents can't fit the old (run, URL) key; evidence is never deleted here.
    n = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM source_documents WHERE attempt <> 'direct'")).scalar()
    if n:
        raise RuntimeError(f"{n} Firecrawl document row(s) exist: remove them deliberately "
                           "before downgrading below 0023")
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.drop_constraint('uq_source_documents_run_url_attempt', type_='unique')
        batch_op.create_unique_constraint('uq_source_documents_run_url',
                                          ['run_id', 'canonical_url'])
        batch_op.drop_column('escalation')
        batch_op.drop_column('attempt')
