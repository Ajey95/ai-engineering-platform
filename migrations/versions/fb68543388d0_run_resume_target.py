"""run resume target

Revision ID: fb68543388d0
Revises: 4fba39f6713d
Create Date: 2026-10-02 16:22:48.395456

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fb68543388d0"
down_revision = "4fba39f6713d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("resume_target", sa.String(length=40), nullable=True))
    op.add_column("runs", sa.Column("resume_key", sa.String(length=200), nullable=True))
    op.add_column("runs", sa.Column("resume_input_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("runs", "resume_input_hash")
    op.drop_column("runs", "resume_key")
    op.drop_column("runs", "resume_target")
