"""Durable tenant-scoped operational alerts and transitions.

Revision ID: e4f23aa7190b
Revises: a9db61e2c7f4
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e4f23aa7190b"
down_revision = "a9db61e2c7f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("alert_id", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("source_cursor", sa.String(36)),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.CheckConstraint(
            "state IN ('observing', 'firing', 'resolved')",
            name="ck_operational_alert_state",
        ),
        sa.UniqueConstraint("tenant_id", "alert_id", name="uq_operational_alert_tenant_kind"),
    )
    op.create_index("ix_operational_alerts_tenant_id", "operational_alerts", ["tenant_id"])
    op.create_table(
        "operational_alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "alert_id", sa.String(36), sa.ForeignKey("operational_alerts.id"), nullable=False
        ),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_operational_alert_events_alert_id", "operational_alert_events", ["alert_id"]
    )
    op.create_index(
        "ix_operational_alert_events_tenant_id", "operational_alert_events", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_operational_alert_events_tenant_id", table_name="operational_alert_events")
    op.drop_index("ix_operational_alert_events_alert_id", table_name="operational_alert_events")
    op.drop_table("operational_alert_events")
    op.drop_index("ix_operational_alerts_tenant_id", table_name="operational_alerts")
    op.drop_table("operational_alerts")
