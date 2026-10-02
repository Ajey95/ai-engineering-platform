"""record independently deletable browser evidence

Revision ID: c53718b2a844
Revises: b8c6d3259e41
"""

import sqlalchemy as sa
from alembic import op

revision = "c53718b2a844"
down_revision = "b8c6d3259e41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_deletions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name="fk_recording_deletions_run_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", "label", name="uq_recording_deletion_scope"
        ),
        sa.CheckConstraint(
            "label IN ('baseline', 'candidate')", name="ck_recording_deletion_label"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'failed')",
            name="ck_recording_deletion_status",
        ),
    )
    op.create_index("ix_recording_deletions_tenant_id", "recording_deletions", ["tenant_id"])
    op.create_index("ix_recording_deletions_run_id", "recording_deletions", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_recording_deletions_run_id", table_name="recording_deletions")
    op.drop_index("ix_recording_deletions_tenant_id", table_name="recording_deletions")
    op.drop_table("recording_deletions")
