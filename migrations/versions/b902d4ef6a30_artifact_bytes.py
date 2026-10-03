"""Reserve tenant private-artifact bytes before publication.

Revision ID: b902d4ef6a30
Revises: a804e7b9c122
"""

import sqlalchemy as sa
from alembic import op

revision = "b902d4ef6a30"
down_revision = "a804e7b9c122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column(
        "artifact_cap_bytes", sa.BigInteger(), nullable=False,
        server_default="5000000000",
    ))
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint("ck_tenant_artifact_cap_positive", "tenants",
                                   "artifact_cap_bytes > 0")
    op.create_table(
        "artifact_charges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("logical_key", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"],
            name="fk_artifact_charge_run_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", "kind", "logical_key",
            name="uq_artifact_charge_logical",
        ),
        sa.CheckConstraint("byte_count > 0", name="ck_artifact_charge_bytes_positive"),
        sa.CheckConstraint(
            "status IN ('reserved', 'active', 'deleted')",
            name="ck_artifact_charge_status",
        ),
    )
    op.create_index("ix_artifact_charges_tenant_id", "artifact_charges", ["tenant_id"])
    op.create_index("ix_artifact_charges_run_id", "artifact_charges", ["run_id"])
    op.execute(sa.text("""
        INSERT INTO artifact_charges (
            id, tenant_id, run_id, kind, logical_key, sha256, byte_count,
            status, created_at, changed_at
        )
        SELECT id, tenant_id, run_id, 'private_media', label,
               manifest_sha256, byte_count, 'active', created_at, created_at
        FROM private_media_publications WHERE status = 'ready'
    """))


def downgrade() -> None:
    op.drop_index("ix_artifact_charges_run_id", "artifact_charges")
    op.drop_index("ix_artifact_charges_tenant_id", "artifact_charges")
    op.drop_table("artifact_charges")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("ck_tenant_artifact_cap_positive", "tenants")
    op.drop_column("tenants", "artifact_cap_bytes")
