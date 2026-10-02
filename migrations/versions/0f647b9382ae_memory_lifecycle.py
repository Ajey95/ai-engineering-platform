"""Complete canonical fact lifecycle and scoped provenance history.

Revision ID: 0f647b9382ae
Revises: cbd675bb9a11
"""

import sqlalchemy as sa
from alembic import op

revision = "0f647b9382ae"
down_revision = "cbd675bb9a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_memory_facts_scope_id", "memory_facts", ["tenant_id", "project_id", "id"]
    )
    op.create_check_constraint(
        "ck_memory_fact_status", "memory_facts",
        "status IN ('proposed', 'verified', 'rejected', 'superseded', 'expired', 'deleted')",
    )
    op.create_table(
        "memory_fact_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("fact_id", sa.String(36), nullable=False),
        sa.Column("previous_status", sa.String(24), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "fact_id"],
            ["memory_facts.tenant_id", "memory_facts.project_id", "memory_facts.id"],
            name="fk_memory_fact_events_fact_scope",
        ),
    )
    for column in ("tenant_id", "project_id", "fact_id"):
        op.create_index(f"ix_memory_fact_events_{column}", "memory_fact_events", [column])


def downgrade() -> None:
    for column in ("fact_id", "project_id", "tenant_id"):
        op.drop_index(f"ix_memory_fact_events_{column}", table_name="memory_fact_events")
    op.drop_table("memory_fact_events")
    op.drop_constraint("ck_memory_fact_status", "memory_facts", type_="check")
    op.drop_constraint("uq_memory_facts_scope_id", "memory_facts", type_="unique")
