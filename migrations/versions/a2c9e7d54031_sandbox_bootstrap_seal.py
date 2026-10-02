"""Persist encrypted guest bootstrap and metadata-sealing receipt.

Revision ID: a2c9e7d54031
Revises: b6714d7c2a09
"""

import sqlalchemy as sa
from alembic import op

revision = "a2c9e7d54031"
down_revision = "b6714d7c2a09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_leases", sa.Column("bootstrap_envelope", sa.Text(), nullable=True))
    op.add_column("sandbox_leases", sa.Column("bootstrap_sha256", sa.String(64), nullable=True))
    op.add_column("sandbox_leases", sa.Column("source_sha256", sa.String(64), nullable=True))
    op.add_column("sandbox_leases", sa.Column(
        "sealed_at", sa.DateTime(timezone=True), nullable=True
    ))
    op.drop_constraint("ck_sandbox_state", "sandbox_leases", type_="check")
    op.create_check_constraint(
        "ck_sandbox_state", "sandbox_leases",
        "state IN ('intended', 'bootstrapping', 'provisioned', 'revoked', "
        "'terminating', 'terminated')",
    )


def downgrade() -> None:
    op.execute("UPDATE sandbox_leases SET state = 'revoked' WHERE state = 'bootstrapping'")
    op.drop_constraint("ck_sandbox_state", "sandbox_leases", type_="check")
    op.create_check_constraint(
        "ck_sandbox_state", "sandbox_leases",
        "state IN ('intended', 'provisioned', 'revoked', 'terminating', 'terminated')",
    )
    op.drop_column("sandbox_leases", "sealed_at")
    op.drop_column("sandbox_leases", "source_sha256")
    op.drop_column("sandbox_leases", "bootstrap_sha256")
    op.drop_column("sandbox_leases", "bootstrap_envelope")
