"""Record optional attested cached-token prices.

Revision ID: d13c76a3f9b2
Revises: c78b82d1fa40
"""

import sqlalchemy as sa
from alembic import op

revision = "d13c76a3f9b2"
down_revision = "c78b82d1fa40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_entries", sa.Column(
        "price_per_m_cache_read", sa.Numeric(12, 6), nullable=True,
    ))
    op.add_column("model_entries", sa.Column(
        "price_per_m_cache_write", sa.Numeric(12, 6), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("model_entries", "price_per_m_cache_write")
    op.drop_column("model_entries", "price_per_m_cache_read")
