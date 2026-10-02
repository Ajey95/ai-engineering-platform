"""Persist the guest output receipt without claiming a repair verdict.

Revision ID: d4c05e73b28a
Revises: a2c9e7d54031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4c05e73b28a"
down_revision = "a2c9e7d54031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_leases", sa.Column("result_sha256", sa.String(64), nullable=True))
    op.add_column("sandbox_leases", sa.Column(
        "result_summary", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=True,
    ))
    op.add_column("sandbox_leases", sa.Column(
        "result_received_at", sa.DateTime(timezone=True), nullable=True
    ))


def downgrade() -> None:
    op.drop_column("sandbox_leases", "result_received_at")
    op.drop_column("sandbox_leases", "result_summary")
    op.drop_column("sandbox_leases", "result_sha256")
