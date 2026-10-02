"""Fence publication dispatch after a worker crash.

Revision ID: e71d5a4b8c20
Revises: f6092c41a8e5
"""

import sqlalchemy as sa
from alembic import op

revision = "e71d5a4b8c20"
down_revision = "f6092c41a8e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column(
        "processing_token", sa.String(36), nullable=True
    ))
    op.add_column("outbox_events", sa.Column(
        "processing_lease_until", sa.DateTime(timezone=True), nullable=True
    ))


def downgrade() -> None:
    op.drop_column("outbox_events", "processing_lease_until")
    op.drop_column("outbox_events", "processing_token")
