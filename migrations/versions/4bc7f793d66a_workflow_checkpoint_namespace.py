"""Reserve an isolated namespace for LangGraph checkpoints.

Revision ID: 4bc7f793d66a
Revises: eb8f0a7d36c4
"""

from alembic import op

revision = "4bc7f793d66a"
down_revision = "eb8f0a7d36c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE SCHEMA IF NOT EXISTS aip_workflow")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # An occupied checkpoint schema must be retired explicitly, never by downgrade.
        op.execute("DROP SCHEMA IF EXISTS aip_workflow")
