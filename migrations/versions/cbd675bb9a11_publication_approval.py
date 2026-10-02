"""Bind explicit draft PR approval to repository and verification evidence.

Revision ID: cbd675bb9a11
Revises: e2466a8aa82c
"""

import sqlalchemy as sa
from alembic import op

revision = "cbd675bb9a11"
down_revision = "e2466a8aa82c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_repository_connections_scope_id", "repository_connections",
        ["tenant_id", "project_id", "id"],
    )
    op.create_unique_constraint(
        "uq_runs_scope_id", "runs", ["tenant_id", "project_id", "id"]
    )
    op.create_table(
        "publication_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("destination", sa.String(300), nullable=False),
        sa.Column("base_commit", sa.String(40), nullable=False),
        sa.Column("patch_sha256", sa.String(64), nullable=False),
        sa.Column("test_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["runs.tenant_id", "runs.project_id", "runs.id"],
            name="fk_publication_approvals_run_scope",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"],
            name="fk_publication_approvals_project_scope",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "connection_id"],
            [
                "repository_connections.tenant_id", "repository_connections.project_id",
                "repository_connections.id",
            ],
            name="fk_publication_approvals_connection_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", "action", name="uq_publication_approval_run_action"
        ),
        sa.CheckConstraint("action = 'draft_pr'", name="ck_publication_approval_action"),
        sa.CheckConstraint(
            "status IN ('approved', 'revoked', 'consumed')",
            name="ck_publication_approval_status",
        ),
    )
    for column in ("tenant_id", "project_id", "run_id"):
        op.create_index(f"ix_publication_approvals_{column}", "publication_approvals", [column])


def downgrade() -> None:
    for column in ("run_id", "project_id", "tenant_id"):
        op.drop_index(f"ix_publication_approvals_{column}", table_name="publication_approvals")
    op.drop_table("publication_approvals")
    op.drop_constraint("uq_runs_scope_id", "runs", type_="unique")
    op.drop_constraint(
        "uq_repository_connections_scope_id", "repository_connections", type_="unique"
    )
