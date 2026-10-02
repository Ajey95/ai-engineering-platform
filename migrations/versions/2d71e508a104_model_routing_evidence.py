"""Tenant model routing authorization and measured evidence.

Revision ID: 2d71e508a104
Revises: 4bc7f793d66a
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2d71e508a104"
down_revision = "4bc7f793d66a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "model_routing_policy",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_table(
        "model_routing_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_entry_id", sa.String(100), nullable=False),
        sa.Column("registry_revision", sa.String(100), nullable=False),
        sa.Column("task_class", sa.String(80), nullable=False),
        sa.Column("suite_revision", sa.String(100), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("success_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=False),
        sa.Column("mean_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sample_count >= 0", name="ck_model_routing_samples"),
        sa.CheckConstraint(
            "success_rate >= 0 AND success_rate <= 1", name="ck_model_routing_success_rate"
        ),
        sa.CheckConstraint("p95_latency_ms >= 0", name="ck_model_routing_latency"),
        sa.CheckConstraint("mean_cost_usd >= 0", name="ck_model_routing_cost"),
        sa.ForeignKeyConstraint(["model_entry_id"], ["model_entries.id"]),
        sa.UniqueConstraint(
            "model_entry_id", "registry_revision", "task_class", "suite_revision",
            name="uq_model_routing_evidence_revision",
        ),
    )
    op.create_index(
        "ix_model_routing_evidence_model_entry_id", "model_routing_evidence", ["model_entry_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_model_routing_evidence_model_entry_id", table_name="model_routing_evidence")
    op.drop_table("model_routing_evidence")
    op.drop_column("tenants", "model_routing_policy")
