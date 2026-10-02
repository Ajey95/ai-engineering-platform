"""Persist scoped per-run VM intent and cleanup state.

Revision ID: ef86c2bb703d
Revises: d8c107a4f0b5
"""

import sqlalchemy as sa
from alembic import op

revision = "ef86c2bb703d"
down_revision = "d8c107a4f0b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sandbox_leases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_fence", sa.Integer(), nullable=False),
        sa.Column("client_token", sa.String(64), nullable=False),
        sa.Column("instance_id", sa.String(32), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("image_id", sa.String(32), nullable=False),
        sa.Column("instance_type", sa.String(32), nullable=False),
        sa.Column("subnet_id", sa.String(32), nullable=False),
        sa.Column("security_group_id", sa.String(32), nullable=False),
        sa.Column("root_device_name", sa.String(32), nullable=False),
        sa.Column("disk_gib", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["runs.tenant_id", "runs.project_id", "runs.id"],
            name="fk_sandbox_lease_run_scope",
        ),
        sa.UniqueConstraint("tenant_id", "run_id", "generation", name="uq_sandbox_generation"),
        sa.UniqueConstraint("client_token", name="uq_sandbox_client_token"),
        sa.UniqueConstraint("instance_id", name="uq_sandbox_instance_id"),
        sa.CheckConstraint("generation > 0", name="ck_sandbox_generation_positive"),
        sa.CheckConstraint("disk_gib BETWEEN 8 AND 100", name="ck_sandbox_disk_bounds"),
        sa.CheckConstraint(
            "state IN ('intended', 'provisioned', 'revoked', 'terminating', 'terminated')",
            name="ck_sandbox_state",
        ),
    )
    op.create_index("ix_sandbox_leases_tenant_id", "sandbox_leases", ["tenant_id"])
    op.create_index("ix_sandbox_leases_run_id", "sandbox_leases", ["run_id"])
    op.create_index("ix_sandbox_leases_expiry", "sandbox_leases", ["state", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sandbox_leases_expiry", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_run_id", table_name="sandbox_leases")
    op.drop_index("ix_sandbox_leases_tenant_id", table_name="sandbox_leases")
    op.drop_table("sandbox_leases")
