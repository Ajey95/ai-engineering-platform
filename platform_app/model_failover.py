"""Authorize one cross-provider switch after a definite rejected model call."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.model_qualification import qualification_current
from platform_app.model_routing import (
    RoutingError,
    validate_failover_routes,
    validate_routing_policy,
)
from platform_app.models import ModelEntry, Project, Run, ToolAction
from platform_app.run_ledger import assert_fence
from platform_app.service import append_event
from platform_app.tenant_quota import QuotaError, lock_tenant


def authorize_rate_limit_failover(
    db: Session, run: Run, worker_id: str, fence: int, rejected: ToolAction,
) -> str | None:
    """Return the next step only when both admission and current policy authorize it."""
    assert_fence(run, worker_id, fence)
    snapshot = run.config_snapshot
    if (
        run.cancel_requested or snapshot.get("model_failover")
        or int(snapshot.get("max_model_calls", 0)) < 2
        or rejected.tenant_id != run.tenant_id or rejected.run_id != run.id
        or rejected.logical_action != "model.generate" or rejected.step_id != "model-1"
        or rejected.status != "COMPLETED"
        or (rejected.receipt or {}).get("status") != "REJECTED"
        or (rejected.receipt or {}).get("error_code") != "PROVIDER_RATE_LIMITED"
        or (rejected.receipt or {}).get("http_status") != 429
    ):
        return None
    pending = db.scalar(select(ToolAction.id).where(
        ToolAction.tenant_id == run.tenant_id,
        ToolAction.run_id == run.id,
        ToolAction.status == "INTENDED",
    ).limit(1))
    if pending is not None:
        return None
    try:
        tenant = lock_tenant(db, run.tenant_id, require_active=False)
    except QuotaError:
        return None
    project = db.get(Project, run.project_id)
    if (
        tenant is None or tenant.status != "active"
        or tenant.policy_revision != snapshot.get("policy_version")
        or project is None or project.tenant_id != run.tenant_id
    ):
        return None
    data_class = (snapshot.get("environment_manifest") or {}).get(
        "data_class", "source_code"
    )
    if (project.environment_manifest or {}).get("data_class", "source_code") != data_class:
        return None
    current_policy = tenant.model_routing_policy or {}
    try:
        current_routes = validate_failover_routes(current_policy)
        if current_policy.get("enabled") is True:
            validate_routing_policy(current_policy)
        elif current_policy.get("enabled") is not False or set(current_policy) not in (
            {"enabled"}, {"enabled", "failover_routes"}
        ):
            return None
    except RoutingError:
        return None
    source = db.get(ModelEntry, run.model_entry_id)
    if source is None:
        return None
    approved = [
        route for route in snapshot.get("model_failover_routes", [])
        if route.get("from_model_entry_id") == source.id
        and data_class in route.get("data_classes", [])
        and route in current_routes
    ]
    if len(approved) != 1:
        return None
    target = db.get(ModelEntry, approved[0]["to_model_entry_id"])
    if current_policy.get("enabled") is True and (
        data_class not in current_policy.get("allowed_data_classes", [])
        or target is None
        or target.id not in current_policy.get("allowed_model_entry_ids", [])
    ):
        return None
    if (
        target is None or target.provider == source.provider or target.state != "enabled"
        or not qualification_current(target)
        or (target.context_limit or 0) < int(snapshot["model_context_limit"])
        or (target.output_limit or 0) < int(snapshot["model_output_limit"])
        or not target.price_revision or target.price_per_m_input is None
        or target.price_per_m_output is None
    ):
        return None
    next_step = "model-2"
    if db.scalar(select(ToolAction.id).where(
        ToolAction.run_id == run.id, ToolAction.step_id == next_step,
    ).limit(1)) is not None:
        return None
    updated = dict(snapshot)
    updated.update({
        "model_registry_revision": target.registry_revision,
        "model_price_revision": target.price_revision,
        "model_context_limit": target.context_limit,
        "model_output_limit": target.output_limit,
        "model_price_per_m_input": str(target.price_per_m_input),
        "model_price_per_m_output": str(target.price_per_m_output),
        "model_id": target.model_id,
        "model_provider": target.provider,
        "model_failover": {
            "source_model_entry_id": source.id,
            "target_model_entry_id": target.id,
            "source_step_id": rejected.step_id,
            "target_step_id": next_step,
            "reason": "PROVIDER_RATE_LIMITED",
        },
    })
    run.model_entry_id = target.id
    run.config_snapshot = updated
    append_event(db, run, "model.failover", updated["model_failover"])
    return next_step
