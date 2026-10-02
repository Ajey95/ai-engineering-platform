"""tenant inference and concurrent run quotas

Revision ID: eb8f0a7d36c4
Revises: c53718b2a844
"""

import sqlalchemy as sa
from alembic import op

revision = "eb8f0a7d36c4"
down_revision = "c53718b2a844"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "daily_inference_cap_usd", sa.Numeric(12, 6), nullable=False, server_default="50"
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "monthly_inference_cap_usd", sa.Numeric(12, 6), nullable=False, server_default="500"
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="4"),
    )
    op.create_check_constraint(
        "ck_tenants_daily_inference_cap", "tenants", "daily_inference_cap_usd > 0"
    )
    op.create_check_constraint(
        "ck_tenants_monthly_inference_cap", "tenants", "monthly_inference_cap_usd > 0"
    )
    op.create_check_constraint(
        "ck_tenants_max_concurrent_runs", "tenants", "max_concurrent_runs > 0"
    )
    op.create_index(
        "ix_budget_ledger_tenant_category_created",
        "budget_ledger",
        ["tenant_id", "category", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_budget_ledger_tenant_category_created", table_name="budget_ledger")
    op.drop_constraint("ck_tenants_max_concurrent_runs", "tenants", type_="check")
    op.drop_constraint("ck_tenants_monthly_inference_cap", "tenants", type_="check")
    op.drop_constraint("ck_tenants_daily_inference_cap", "tenants", type_="check")
    op.drop_column("tenants", "max_concurrent_runs")
    op.drop_column("tenants", "monthly_inference_cap_usd")
    op.drop_column("tenants", "daily_inference_cap_usd")
