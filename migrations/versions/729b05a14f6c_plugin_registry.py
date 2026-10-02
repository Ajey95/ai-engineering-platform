"""Pinned tool plugin registry and tenant allowlist.

Revision ID: 729b05a14f6c
Revises: 2d71e508a104
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "729b05a14f6c"
down_revision = "2d71e508a104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column(
        "tenants",
        sa.Column("plugin_allowlist", json_type, nullable=False, server_default="[]"),
    )
    op.create_table(
        "plugin_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plugin_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(60), nullable=False),
        sa.Column("manifest", json_type, nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plugin_id", "version", name="uq_plugin_version"),
    )
    op.create_index("ix_plugin_entries_plugin_id", "plugin_entries", ["plugin_id"])
    op.create_table(
        "plugin_registry_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plugin_entry_id", sa.String(36), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plugin_entry_id"], ["plugin_entries.id"]),
    )
    op.create_index(
        "ix_plugin_registry_events_plugin_entry_id",
        "plugin_registry_events", ["plugin_entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_registry_events_plugin_entry_id", table_name="plugin_registry_events")
    op.drop_table("plugin_registry_events")
    op.drop_index("ix_plugin_entries_plugin_id", table_name="plugin_entries")
    op.drop_table("plugin_entries")
    op.drop_column("tenants", "plugin_allowlist")
