"""Source classification v2: who published a document or search hit (publisher_kind), under
which rules (classification_version; NULL = classify-1, the rows from before) and, for
documents, the signals that decided it (a copied document keeps its original classification
there). Nullable columns only: existing rows are never rewritten.

Revision ID: 0019_source_classification
Revises: 0018_run_utility_candidates
Create Date: 2026-11-01 15:00:00.000000
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0019_source_classification'
down_revision: str | None = '0018_run_utility_candidates'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

def upgrade() -> None:
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('publisher_kind', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('classification_version', sa.String(length=32),
                                      nullable=True))
        batch_op.add_column(sa.Column('classification_signals', JSON, nullable=True))
    with op.batch_alter_table('discovery_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('publisher_kind', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('classification_version', sa.String(length=32),
                                      nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('discovery_results', schema=None) as batch_op:
        batch_op.drop_column('classification_version')
        batch_op.drop_column('publisher_kind')
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.drop_column('classification_signals')
        batch_op.drop_column('classification_version')
        batch_op.drop_column('publisher_kind')
