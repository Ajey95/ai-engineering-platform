"""Constrain operational alert transitions to their alert's tenant.

Revision ID: b6e1c4a0d9f2
Revises: e4f23aa7190b
"""

from alembic import op

revision = "b6e1c4a0d9f2"
down_revision = "e4f23aa7190b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_operational_alert_scope_id", "operational_alerts", ["tenant_id", "id"]
    )
    op.drop_constraint(
        "operational_alert_events_alert_id_fkey", "operational_alert_events",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_operational_alert_events_scope", "operational_alert_events",
        "operational_alerts", ["tenant_id", "alert_id"], ["tenant_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_operational_alert_events_scope", "operational_alert_events", type_="foreignkey"
    )
    op.create_foreign_key(
        "operational_alert_events_alert_id_fkey", "operational_alert_events",
        "operational_alerts", ["alert_id"], ["id"],
    )
    op.drop_constraint("uq_operational_alert_scope_id", "operational_alerts", type_="unique")
