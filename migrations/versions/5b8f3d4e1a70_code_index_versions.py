"""Canonical revision-pinned code file and symbol index.

Revision ID: 5b8f3d4e1a70
Revises: e71d5a4b8c20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "5b8f3d4e1a70"
down_revision = "e71d5a4b8c20"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "code_index_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("repository_ref", sa.String(300), nullable=False),
        sa.Column("commit", sa.String(64), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("indexed_files", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"],
            name="fk_code_index_project_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "repository_ref", "commit",
            name="uq_code_index_revision",
        ),
        sa.UniqueConstraint("tenant_id", "project_id", "id", name="uq_code_index_scope_id"),
    )
    op.create_index("ix_code_index_snapshots_tenant_id", "code_index_snapshots", ["tenant_id"])
    op.create_index("ix_code_index_snapshots_project_id", "code_index_snapshots", ["project_id"])
    op.create_table(
        "code_file_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("symbols", json_type, nullable=False),
        sa.Column("imports", json_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "snapshot_id"],
            ["code_index_snapshots.tenant_id", "code_index_snapshots.project_id",
             "code_index_snapshots.id"],
            name="fk_code_file_snapshot_scope",
        ),
        sa.UniqueConstraint("snapshot_id", "path", name="uq_code_file_snapshot_path"),
    )
    op.create_index("ix_code_file_versions_snapshot_id", "code_file_versions", ["snapshot_id"])
    op.create_index("ix_code_file_versions_tenant_id", "code_file_versions", ["tenant_id"])
    op.create_index("ix_code_file_versions_project_id", "code_file_versions", ["project_id"])


def downgrade() -> None:
    op.drop_table("code_file_versions")
    op.drop_table("code_index_snapshots")
