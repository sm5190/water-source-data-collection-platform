"""Water-supply relationship graphs: one row per relationship (or rejected statement) per run
and policy version, with the full relationship and its evidence as JSON. New table only.

Revision ID: 0020_run_supply_relationships
Revises: 0019_source_classification
Create Date: 2026-11-03 12:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0020_run_supply_relationships'
down_revision: str | None = '0019_source_classification'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    op.create_table('run_supply_relationships',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('relationship_key', sa.String(length=1000), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('subject_id', sa.String(length=500), nullable=False),
    sa.Column('object_id', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('temporal', sa.String(length=16), nullable=True),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('identity', sa.String(length=32), nullable=False),
    sa.Column('geo_basis', sa.String(length=24), nullable=False),
    sa.Column('payload', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'policy_version', 'relationship_key',
                        name='uq_run_supply_relationships')
    )
    with op.batch_alter_table('run_supply_relationships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_supply_relationships_run_id'), ['run_id'],
                              unique=False)


def downgrade() -> None:
    with op.batch_alter_table('run_supply_relationships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_supply_relationships_run_id'))
    op.drop_table('run_supply_relationships')
