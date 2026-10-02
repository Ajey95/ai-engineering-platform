"""Track durable transport receipt separately from outbox execution status.

Revision ID: b6714d7c2a09
Revises: ef86c2bb703d
"""

import sqlalchemy as sa
from alembic import op

revision = "b6714d7c2a09"
down_revision = "ef86c2bb703d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("queue_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbox_dispatch_publish",
        "outbox_events",
        ["topic", "status", "queue_published_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_dispatch_publish", table_name="outbox_events")
    op.drop_column("outbox_events", "queue_published_at")
