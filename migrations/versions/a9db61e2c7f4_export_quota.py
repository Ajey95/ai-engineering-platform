"""Tenant daily export quota and scoped export ledger.

Revision ID: a9db61e2c7f4
Revises: 0f647b9382ae
"""

import sqlalchemy as sa
from alembic import op

revision = "a9db61e2c7f4"
down_revision = "0f647b9382ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants", sa.Column(
            "daily_export_cap_bytes", sa.Integer(), nullable=False,
            server_default="100000000",
        ),
    )
    op.create_check_constraint(
        "ck_tenant_export_cap_positive", "tenants", "daily_export_cap_bytes > 0"
    )
    op.create_table(
        "export_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("bytes_count", sa.Integer(), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("bytes_count > 0", name="ck_export_ledger_bytes_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"],
            name="fk_export_ledger_run_scope",
        ),
    )
    op.create_index("ix_export_ledger_tenant_id", "export_ledger", ["tenant_id"])
    op.create_index("ix_export_ledger_run_id", "export_ledger", ["run_id"])
    op.create_index(
        "ix_export_ledger_tenant_created", "export_ledger", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_export_ledger_tenant_created", table_name="export_ledger")
    op.drop_index("ix_export_ledger_run_id", table_name="export_ledger")
    op.drop_index("ix_export_ledger_tenant_id", table_name="export_ledger")
    op.drop_table("export_ledger")
    op.drop_constraint("ck_tenant_export_cap_positive", "tenants", type_="check")
    op.drop_column("tenants", "daily_export_cap_bytes")
