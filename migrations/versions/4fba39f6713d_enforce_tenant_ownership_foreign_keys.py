"""Enforce tenant and project ownership in core foreign keys.

Revision ID: 4fba39f6713d
Revises: 0a09a20564b1
"""

from alembic import op

revision = "4fba39f6713d"
down_revision = "0a09a20564b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_tasks_scope_id", "tasks", ["tenant_id", "project_id", "id"])
    op.create_unique_constraint("uq_runs_tenant_id", "runs", ["tenant_id", "id"])

    op.drop_constraint("tasks_project_id_fkey", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_tasks_project_scope",
        "tasks",
        "projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
    )
    op.drop_constraint("runs_task_id_fkey", "runs", type_="foreignkey")
    op.drop_constraint("runs_project_id_fkey", "runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_runs_project_scope",
        "runs",
        "projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_runs_task_scope",
        "runs",
        "tasks",
        ["tenant_id", "project_id", "task_id"],
        ["tenant_id", "project_id", "id"],
    )

    for table, old_name, new_name in (
        ("budget_ledger", "budget_ledger_run_id_fkey", "fk_budget_ledger_run_scope"),
        ("run_events", "run_events_run_id_fkey", "fk_run_events_run_scope"),
        ("tool_actions", "tool_actions_run_id_fkey", "fk_tool_actions_run_scope"),
    ):
        op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(new_name, table, "runs", ["tenant_id", "run_id"], ["tenant_id", "id"])
    op.drop_constraint("memory_facts_project_id_fkey", "memory_facts", type_="foreignkey")
    op.create_foreign_key(
        "fk_memory_facts_project_scope",
        "memory_facts",
        "projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_outbox_events_tenant", "outbox_events", "tenants", ["tenant_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_audit_events_tenant", "audit_events", "tenants", ["tenant_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_tenant", "audit_events", type_="foreignkey")
    op.drop_constraint("fk_outbox_events_tenant", "outbox_events", type_="foreignkey")
    op.drop_constraint("fk_memory_facts_project_scope", "memory_facts", type_="foreignkey")
    op.create_foreign_key(
        "memory_facts_project_id_fkey", "memory_facts", "projects", ["project_id"], ["id"]
    )
    for table, old_name, new_name in (
        ("tool_actions", "tool_actions_run_id_fkey", "fk_tool_actions_run_scope"),
        ("run_events", "run_events_run_id_fkey", "fk_run_events_run_scope"),
        ("budget_ledger", "budget_ledger_run_id_fkey", "fk_budget_ledger_run_scope"),
    ):
        op.drop_constraint(new_name, table, type_="foreignkey")
        op.create_foreign_key(old_name, table, "runs", ["run_id"], ["id"])
    op.drop_constraint("fk_runs_task_scope", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_project_scope", "runs", type_="foreignkey")
    op.create_foreign_key("runs_task_id_fkey", "runs", "tasks", ["task_id"], ["id"])
    op.create_foreign_key("runs_project_id_fkey", "runs", "projects", ["project_id"], ["id"])
    op.drop_constraint("fk_tasks_project_scope", "tasks", type_="foreignkey")
    op.create_foreign_key("tasks_project_id_fkey", "tasks", "projects", ["project_id"], ["id"])
    op.drop_constraint("uq_runs_tenant_id", "runs", type_="unique")
    op.drop_constraint("uq_tasks_scope_id", "tasks", type_="unique")
