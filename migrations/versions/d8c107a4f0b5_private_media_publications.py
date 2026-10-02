"""Track scoped private object publication and deletion.

Revision ID: d8c107a4f0b5
Revises: c2a4d90871e6
"""

import sqlalchemy as sa
from alembic import op

revision = "d8c107a4f0b5"
down_revision = "c2a4d90871e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "private_media_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(16), nullable=False),
        sa.Column("effect_hash", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "run_id", "label", name="uq_private_media_scope"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["runs.tenant_id", "runs.project_id", "runs.id"],
            name="fk_private_media_run_scope",
        ),
        sa.CheckConstraint(
            "label IN ('baseline', 'candidate')", name="ck_private_media_label"
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'deleted')", name="ck_private_media_status"
        ),
        sa.CheckConstraint(
            "object_count > 0 AND byte_count > 0", name="ck_private_media_nonempty"
        ),
    )
    op.create_index(
        "ix_private_media_publications_tenant_id", "private_media_publications", ["tenant_id"]
    )
    op.create_index(
        "ix_private_media_publications_run_id", "private_media_publications", ["run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_private_media_publications_run_id", table_name="private_media_publications")
    op.drop_index(
        "ix_private_media_publications_tenant_id", table_name="private_media_publications"
    )
    op.drop_table("private_media_publications")
