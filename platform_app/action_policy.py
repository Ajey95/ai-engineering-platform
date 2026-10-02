"""Deterministic authorization for effects in the development fixture workflow.

The model cannot grant itself a tool. This module binds each effect to the
persisted tenant, project, run, fixture, reviewed action and source revision.
Hosted execution has no grant until its separate sandbox broker exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.config import settings
from platform_app.models import AuditEvent, Project, Run, Task, Tenant, ToolAction
from platform_app.run_ledger import assert_fence, begin_tool_action
from platform_app.service import ServiceError, canonical_hash
from platform_app.telemetry import set_safe_attributes, tracer


@dataclass(frozen=True)
class ActionIntent:
    step_id: str
    name: str
    action_class: str
    target: str
    source_version: str
    arguments: dict


FIXTURE_ACTIONS = {
    "named": "fixture.named",
    "browser": "fixture.browser",
    "oracle": "fixture.oracle",
    "candidate_named": "fixture.candidate_named",
    "candidate_browser": "fixture.candidate_browser",
    "candidate_oracle": "fixture.candidate_oracle",
}
FIXTURE_VERSION = "development-fixture-v1"
MEDIA_VERSION = "hls-v1"
PATCH_VERSION = "fixture-patch-v1"


def _denial_reason(db: Session, run: Run, intent: ActionIntent) -> str | None:
    tenant = db.get(Tenant, run.tenant_id)
    project = db.scalar(
        select(Project).where(Project.id == run.project_id, Project.tenant_id == run.tenant_id)
    )
    task = db.scalar(
        select(Task).where(
            Task.id == run.task_id,
            Task.project_id == run.project_id,
            Task.tenant_id == run.tenant_id,
        )
    )
    snapshot = run.config_snapshot or {}
    if tenant is None or tenant.status != "active" or project is None or task is None:
        return "scope"
    if snapshot.get("policy_version") != tenant.policy_revision:
        return "policy_revision"
    if settings().environment != "development":
        return "hosted_execution_unavailable"
    if (
        snapshot.get("reproduction", {}).get("fixture_case_id") != "form-submit-001"
        or project.environment_manifest.get("case_id") != "form-submit-001"
        or snapshot.get("environment_manifest") != project.environment_manifest
        or snapshot.get("repository_url") != project.repository_url
        or snapshot.get("test_url") != project.test_url
    ):
        return "fixture_binding"
    if run.cancel_requested or run.state in {"COMPLETED", "FAILED", "CANCELLED", "INCONCLUSIVE"}:
        return "run_closed"
    if not isinstance(snapshot.get("max_tool_calls"), int) or snapshot["max_tool_calls"] < 1:
        return "tool_budget"
    existing = db.scalar(
        select(ToolAction.id).where(
            ToolAction.run_id == run.id,
            ToolAction.step_id == intent.step_id,
            ToolAction.logical_action == intent.name,
        )
    )
    count = db.scalar(
        select(func.count()).select_from(ToolAction).where(ToolAction.run_id == run.id)
    )
    if existing is None and count >= snapshot["max_tool_calls"]:
        return "tool_budget"

    if intent.name in FIXTURE_ACTIONS.values():
        if (
            FIXTURE_ACTIONS.get(intent.step_id) != intent.name
            or intent.action_class != "isolated_execution"
            or intent.target != "form-submit-001"
            or intent.source_version != FIXTURE_VERSION
            or intent.arguments.get("fixture_case_id") != "form-submit-001"
            or intent.arguments.get("base_commit") != run.base_commit
        ):
            return "action_manifest"
    elif intent.name == "fixture.patch":
        if (
            intent.step_id != "candidate_patch"
            or intent.action_class != "workspace_write"
            or intent.target != "server.py"
            or intent.source_version != PATCH_VERSION
            or not isinstance(intent.arguments.get("patch_sha256"), str)
        ):
            return "action_manifest"
    elif intent.name == "media.encode":
        if (
            intent.step_id not in {"media_baseline", "media_candidate"}
            or intent.action_class != "private_artifact_write"
            or intent.target != run.id
            or intent.source_version != MEDIA_VERSION
            or intent.arguments.get("profile_revision") != MEDIA_VERSION
        ):
            return "action_manifest"
    elif intent.name == "model.generate":
        if (
            intent.action_class != "provider_request"
            or intent.target != run.model_entry_id
            or intent.source_version != snapshot.get("model_registry_revision")
            or intent.arguments.get("model_entry_id") != run.model_entry_id
            or intent.arguments.get("model_revision") != intent.source_version
            or intent.arguments.get("price_revision") != snapshot.get("model_price_revision")
        ):
            return "action_manifest"
    else:
        return "action_unregistered"
    return None


@tracer.start_as_current_span("tool.authorize")
def authorize_run_effect(
    db: Session, run: Run, worker_id: str, fence: int, intent: ActionIntent
) -> ToolAction:
    """Persist the decision before the caller can perform an external effect."""
    set_safe_attributes(run_id=run.id, tool_name=intent.name, tool_step=intent.step_id)
    assert_fence(run, worker_id, fence)
    reason = _denial_reason(db, run, intent)
    existing = db.scalar(
        select(ToolAction.id).where(
            ToolAction.run_id == run.id,
            ToolAction.step_id == intent.step_id,
            ToolAction.logical_action == intent.name,
        )
    )
    if reason:
        set_safe_attributes(policy_outcome="denied", policy_reason=reason)
        if existing is None and not run.cancel_requested:
            begin_tool_action(
                db, run, worker_id, fence, intent.step_id, intent.name, intent.arguments, False
            )
        db.add(
            AuditEvent(
                tenant_id=run.tenant_id,
                actor=worker_id,
                action="tool.authorize",
                target_ref=f"{run.id}:{intent.step_id}",
                arguments_hash=canonical_hash(intent.__dict__),
                policy_revision=(run.config_snapshot or {}).get("policy_version", "unknown"),
                outcome=f"denied:{reason}",
            )
        )
        db.commit()
        raise ServiceError("TOOL_DENIED", f"Tool policy denied {reason}", 403)
    action = begin_tool_action(
        db, run, worker_id, fence, intent.step_id, intent.name, intent.arguments, True
    )
    set_safe_attributes(policy_outcome="allowed")
    if existing is None:
        db.add(
            AuditEvent(
                tenant_id=run.tenant_id,
                actor=worker_id,
                action="tool.authorize",
                target_ref=f"{run.id}:{intent.step_id}",
                arguments_hash=canonical_hash(intent.__dict__),
                policy_revision=run.config_snapshot["policy_version"],
                outcome="allowed",
            )
        )
    return action
