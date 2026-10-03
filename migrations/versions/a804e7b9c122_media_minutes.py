"""Reserve media processing duration per tenant and attempt.

Revision ID: a804e7b9c122
Revises: f89e4d70ab12
"""

import sqlalchemy as sa
from alembic import op

revision = "a804e7b9c122"
down_revision = "f89e4d70ab12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column(
        "daily_media_minutes", sa.Integer(), nullable=False, server_default="120",
    ))
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint("ck_tenant_media_cap_positive", "tenants",
                                   "daily_media_minutes > 0")
    op.create_table(
        "media_minute_charges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("reserved_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"],
            name="fk_media_minute_run_scope",
        ),
        sa.UniqueConstraint("tenant_id", "run_id", "label", "attempt",
                            name="uq_media_minute_attempt"),
        sa.CheckConstraint("label IN ('baseline', 'candidate')", name="ck_media_minute_label"),
        sa.CheckConstraint("attempt > 0", name="ck_media_minute_attempt_positive"),
        sa.CheckConstraint("reserved_seconds > 0", name="ck_media_minute_seconds_positive"),
        sa.CheckConstraint(
            "status IN ('reserved', 'completed', 'failed')",
            name="ck_media_minute_status",
        ),
    )
    op.create_index("ix_media_minute_charges_tenant_id", "media_minute_charges", ["tenant_id"])
    op.create_index("ix_media_minute_charges_run_id", "media_minute_charges", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_media_minute_charges_run_id", "media_minute_charges")
    op.drop_index("ix_media_minute_charges_tenant_id", "media_minute_charges")
    op.drop_table("media_minute_charges")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("ck_tenant_media_cap_positive", "tenants")
    op.drop_column("tenants", "daily_media_minutes")
