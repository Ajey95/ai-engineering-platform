"""Reserve and settle per-tenant sandbox minutes.

Revision ID: f89e4d70ab12
Revises: d13c76a3f9b2
"""

import sqlalchemy as sa
from alembic import op

revision = "f89e4d70ab12"
down_revision = "d13c76a3f9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column(
        "daily_sandbox_minutes", sa.Integer(), nullable=False,
        server_default="120",
    ))
    op.add_column("sandbox_leases", sa.Column(
        "reserved_seconds", sa.Integer(), nullable=False, server_default="1800",
    ))
    op.add_column("sandbox_leases", sa.Column("used_seconds", sa.Integer(), nullable=True))
    op.add_column("sandbox_leases", sa.Column(
        "started_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("sandbox_leases", sa.Column(
        "terminated_at", sa.DateTime(timezone=True), nullable=True,
    ))
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint("ck_tenant_sandbox_cap_positive", "tenants",
                                   "daily_sandbox_minutes > 0")
        op.create_check_constraint("ck_sandbox_reserved_positive", "sandbox_leases",
                                   "reserved_seconds > 0")
        op.create_check_constraint("ck_sandbox_used_nonnegative", "sandbox_leases",
                                   "used_seconds IS NULL OR used_seconds >= 0")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("ck_sandbox_used_nonnegative", "sandbox_leases")
        op.drop_constraint("ck_sandbox_reserved_positive", "sandbox_leases")
    op.drop_column("sandbox_leases", "terminated_at")
    op.drop_column("sandbox_leases", "started_at")
    op.drop_column("sandbox_leases", "used_seconds")
    op.drop_column("sandbox_leases", "reserved_seconds")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("ck_tenant_sandbox_cap_positive", "tenants")
    op.drop_column("tenants", "daily_sandbox_minutes")
