"""record model registry lifecycle events

Revision ID: b8c6d3259e41
Revises: fb68543388d0
"""

import sqlalchemy as sa
from alembic import op

revision = "b8c6d3259e41"
down_revision = "fb68543388d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_registry_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("model_entry_id", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("registry_revision", sa.String(length=100), nullable=False),
        sa.Column("metadata_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_entry_id"], ["model_entries.id"]),
    )
    op.create_index(
        "ix_model_registry_events_model_entry_id",
        "model_registry_events",
        ["model_entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_registry_events_model_entry_id", table_name="model_registry_events")
    op.drop_table("model_registry_events")
