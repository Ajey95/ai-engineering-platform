"""Distinguish baseline and candidate guest VM executions.

Revision ID: f6092c41a8e5
Revises: d4c05e73b28a
"""

import sqlalchemy as sa
from alembic import op

revision = "f6092c41a8e5"
down_revision = "d4c05e73b28a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_leases", sa.Column(
        "phase", sa.String(16), nullable=False, server_default="baseline"
    ))
    op.create_check_constraint(
        "ck_sandbox_phase", "sandbox_leases", "phase IN ('baseline', 'candidate')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_sandbox_phase", "sandbox_leases", type_="check")
    op.drop_column("sandbox_leases", "phase")
