"""Claim citations: the quote's verbatim character span in its chunk (set by validation when
the quote occurs there exactly; NULL otherwise and for older rows). Additive.

Revision ID: 0022_claim_citation_spans
Revises: 0021_document_links
Create Date: 2026-11-04 00:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = '0022_claim_citation_spans'
down_revision: str | None = '0021_document_links'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    with op.batch_alter_table('claim_citations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('char_start', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('char_end', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('claim_citations', schema=None) as batch_op:
        batch_op.drop_column('char_end')
        batch_op.drop_column('char_start')
