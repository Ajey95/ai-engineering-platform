"""Record the reason for each model lifecycle transition.

Revision ID: c78b82d1fa40
Revises: 5b8f3d4e1a70
"""

import sqlalchemy as sa
from alembic import op

revision = "c78b82d1fa40"
down_revision = "5b8f3d4e1a70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_registry_events", sa.Column("reason", sa.String(2000), nullable=True))


def downgrade() -> None:
    op.drop_column("model_registry_events", "reason")
