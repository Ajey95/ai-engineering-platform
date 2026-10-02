"""Persist at-least-once operational alert delivery state.

Revision ID: f79e418c624a
Revises: b6e1c4a0d9f2
"""

import sqlalchemy as sa
from alembic import op

revision = "f79e418c624a"
down_revision = "b6e1c4a0d9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operational_alert_events",
        sa.Column("notification_status", sa.String(20), nullable=False,
                  server_default="not_required"),
    )
    op.add_column(
        "operational_alert_events",
        sa.Column("notification_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "operational_alert_events",
        sa.Column("notification_next_attempt_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "operational_alert_events",
        sa.Column("notification_delivered_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "operational_alert_events",
        sa.Column("notification_last_error", sa.String(40)),
    )
    op.create_check_constraint(
        "ck_operational_alert_event_notification_status", "operational_alert_events",
        "notification_status IN ('not_required', 'pending', 'delivered')",
    )
    op.create_index(
        "ix_operational_alert_delivery_queue", "operational_alert_events",
        ["notification_status", "notification_next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operational_alert_delivery_queue", table_name="operational_alert_events")
    op.drop_constraint(
        "ck_operational_alert_event_notification_status", "operational_alert_events",
        type_="check",
    )
    op.drop_column("operational_alert_events", "notification_last_error")
    op.drop_column("operational_alert_events", "notification_delivered_at")
    op.drop_column("operational_alert_events", "notification_next_attempt_at")
    op.drop_column("operational_alert_events", "notification_attempts")
    op.drop_column("operational_alert_events", "notification_status")
