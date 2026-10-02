"""Server-side browser sessions for hosted OIDC login.

Revision ID: c2a4d90871e6
Revises: f79e418c624a
"""

import sqlalchemy as sa
from alembic import op

revision = "c2a4d90871e6"
down_revision = "f79e418c624a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_browser_sessions_tenant_id", "browser_sessions", ["tenant_id"])
    op.create_index(
        "ix_browser_sessions_tenant_subject", "browser_sessions", ["tenant_id", "subject"]
    )
    op.create_index("ix_browser_sessions_expires_at", "browser_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_browser_sessions_expires_at", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_tenant_subject", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_tenant_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
