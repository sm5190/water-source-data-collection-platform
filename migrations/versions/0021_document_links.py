"""Outbound links of acquired HTML pages (target, anchor, context, heading, footer flag,
basis PARSED/REPARSED): context for municipal affiliation. New table only.

Revision ID: 0021_document_links
Revises: 0020_run_supply_relationships
Create Date: 2026-11-03 18:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = '0021_document_links'
down_revision: str | None = '0020_run_supply_relationships'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('document_links',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('source_url', sa.String(length=2048), nullable=False),
    sa.Column('link_key', sa.String(length=40), nullable=False),
    sa.Column('target_url', sa.String(length=2048), nullable=False),
    sa.Column('target_host', sa.String(length=255), nullable=False),
    sa.Column('anchor_text', sa.String(length=500), nullable=False),
    sa.Column('context', sa.String(length=300), nullable=False),
    sa.Column('heading', sa.String(length=300), nullable=False),
    sa.Column('in_footer', sa.Boolean(), nullable=False),
    sa.Column('basis', sa.String(length=16), nullable=False),
    sa.Column('observed_at', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'document_id', 'link_key', name='uq_document_links')
    )
    with op.batch_alter_table('document_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_links_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_links_document_id'), ['document_id'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('document_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_links_document_id'))
        batch_op.drop_index(batch_op.f('ix_document_links_run_id'))
    op.drop_table('document_links')
