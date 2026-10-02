"""Scoped GitHub repository connection records.

Revision ID: e2466a8aa82c
Revises: 729b05a14f6c
"""

import sqlalchemy as sa
from alembic import op

revision = "e2466a8aa82c"
down_revision = "729b05a14f6c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repository_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("repository_ref", sa.String(220), nullable=False),
        sa.Column("credential_ref", sa.String(300), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"],
            name="fk_repository_connections_project_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "provider", "repository_ref",
            name="uq_repository_connections_scope_ref",
        ),
        sa.CheckConstraint("provider = 'github'", name="ck_repository_connection_provider"),
        sa.CheckConstraint(
            "status IN ('unverified', 'ready', 'disabled')",
            name="ck_repository_connection_status",
        ),
    )
    op.create_index("ix_repository_connections_tenant_id", "repository_connections", ["tenant_id"])
    op.create_index(
        "ix_repository_connections_project_id", "repository_connections", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_repository_connections_project_id", table_name="repository_connections")
    op.drop_index("ix_repository_connections_tenant_id", table_name="repository_connections")
    op.drop_table("repository_connections")
