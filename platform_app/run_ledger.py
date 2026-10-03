"""Run ownership and side effect ledger. See FR-HAR-04 and AC-05/06."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.config import settings
from platform_app.db import utcnow
from platform_app.model_qualification import qualification_for_pinned_run
from platform_app.models import (
    AuditEvent,
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Run,
    RunEvent,
    SandboxLease,
    Tenant,
    ToolAction,
)
from platform_app.pause_integrity import pause_event_payload, verify_pause_checkpoint
from platform_app.service import ServiceError, append_event, canonical_hash
from platform_app.telemetry import inject_trace, set_safe_attributes, tracer

ACTIVE_STATES = {"PREPARING", "REPRODUCING", "INVESTIGATING", "PATCHING", "VERIFYING"}
TERMINAL_STATES = {"COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"}


def aware(value: datetime) -> datetime:
    # SQLite development tests return naive datetimes despite timezone=True.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


ALLOWED_TRANSITIONS = {
    "QUEUED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"REPRODUCING", "FAILED", "PAUSED_INPUT", "PAUSED_APPROVAL", "CANCEL_REQUESTED"},
    "REPRODUCING": {
        "INVESTIGATING",
        "INCONCLUSIVE",
        "FAILED",
        "PAUSED_INPUT",
        "PAUSED_APPROVAL",
        "CANCEL_REQUESTED",
    },
    "INVESTIGATING": {
        "PATCHING",
        "INCONCLUSIVE",
        "FAILED",
        "PAUSED_INPUT",
        "PAUSED_APPROVAL",
        "PAUSED_BUDGET",
        "CANCEL_REQUESTED",
    },
    "PATCHING": {"VERIFYING", "FAILED", "PAUSED_BUDGET", "PAUSED_APPROVAL", "CANCEL_REQUESTED"},
    "VERIFYING": {
        "PATCHING", "REVIEW_READY", "FAILED", "INCONCLUSIVE",
        "PAUSED_APPROVAL", "PAUSED_BUDGET", "CANCEL_REQUESTED",
    },
    "REVIEW_READY": {"PATCHING", "COMPLETED", "PAUSED_APPROVAL", "CANCEL_REQUESTED"},
    "PAUSED_INPUT": {"QUEUED", "CANCELLED"},
    "PAUSED_BUDGET": {"QUEUED", "CANCELLED"},
    "PAUSED_APPROVAL": {"QUEUED", "CANCELLED"},
    "CANCEL_REQUESTED": {"CANCELLED"},
}


def claim_run(db: Session, run_id: str, worker_id: str, lease_seconds: int = 60) -> tuple[Run, int]:
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    now = utcnow()
    if run.cancel_requested or run.state in TERMINAL_STATES or run.state.startswith("PAUSED"):
        raise ServiceError("RUN_CLOSED", "Run is not executable", 409)
    if run.lease_until and aware(run.lease_until) > now and run.lease_owner != worker_id:
        raise ServiceError("LEASE_HELD", "Run has an active worker", 409)
    run.lease_fence += 1
    run.lease_owner = worker_id
    run.lease_until = now + timedelta(seconds=lease_seconds)
    append_event(db, run, "run.state_changed", {"state": run.state, "worker_claimed": True})
    return run, run.lease_fence


def assert_fence(run: Run, worker_id: str, fence: int) -> None:
    if run.lease_owner != worker_id or run.lease_fence != fence:
        raise ServiceError("LEASE_LOST", "Worker no longer owns this run", 409)
    if run.lease_until is None or aware(run.lease_until) <= utcnow():
        raise ServiceError("LEASE_LOST", "Run lease expired", 409)


def heartbeat(
    db: Session, run_id: str, worker_id: str, fence: int, lease_seconds: int = 60
) -> None:
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    assert_fence(run, worker_id, fence)
    run.lease_until = utcnow() + timedelta(seconds=lease_seconds)


def transition(
    db: Session, run: Run, worker_id: str, fence: int, next_state: str, verdict: str | None = None
) -> None:
    assert_fence(run, worker_id, fence)
    if run.cancel_requested and next_state != "CANCELLED":
        raise ServiceError("RUN_CANCELLED", "Cancellation stops new work", 409)
    resumed_checkpoint = (
        run.state == "QUEUED"
        and bool(run.resume_key)
        and run.resume_target == next_state
        and next_state in ACTIVE_STATES
    )
    if next_state not in ALLOWED_TRANSITIONS.get(run.state, set()) and not resumed_checkpoint:
        raise ServiceError("INVALID_TRANSITION", f"Cannot move {run.state} to {next_state}", 409)
    previous_state = run.state
    run.state = next_state
    if next_state.startswith("PAUSED"):
        run.resume_target = previous_state
        # Pausing releases capacity in the same canonical transaction. Cleanup
        # reconciles a launch with no stored instance ID by its client token.
        leases = db.scalars(select(SandboxLease).where(
            SandboxLease.tenant_id == run.tenant_id,
            SandboxLease.run_id == run.id,
            SandboxLease.state.in_(["intended", "bootstrapping", "provisioned"]),
        ).with_for_update()).all()
        for lease in leases:
            lease.state = "revoked"
            lease.updated_at = utcnow()
            db.add(OutboxEvent(
                tenant_id=run.tenant_id, topic="sandbox.cleanup",
                payload={"run_id": run.id, "sandbox_lease_id": lease.id, **inject_trace()},
            ))
            append_event(db, run, "sandbox.revoked", {
                "sandbox_lease_id": lease.id, "reason": "paused",
            })
    if verdict is not None:
        if verdict not in {"PASSED", "FAILED", "INCONCLUSIVE", "NOT_RUN"}:
            raise ServiceError("INVALID_VERDICT", "Unknown verification verdict", 400)
        run.verdict = verdict
    payload = (
        pause_event_payload(db, run) if next_state.startswith("PAUSED")
        else {"state": next_state, "verdict": run.verdict}
    )
    append_event(db, run, "run.state_changed", payload)
    if next_state in TERMINAL_STATES:
        append_event(db, run, "run.closed", {"state": next_state, "verdict": run.verdict})
    if (
        next_state in TERMINAL_STATES
        or next_state.startswith("PAUSED")
        or next_state == "REVIEW_READY"
    ):
        run.lease_owner = None
        run.lease_until = None


@tracer.start_as_current_span("run.resume")
def resume_input_run(
    db: Session,
    tenant_id: str,
    run_id: str,
    actor: str,
    input_text: str,
    idempotency_key: str,
) -> Run:
    """Requeue a paused input run only after checking policy and effect uncertainty."""
    set_safe_attributes(run_id=run_id, tenant_id=tenant_id)
    if settings().environment != "development":
        raise ServiceError("EXECUTION_UNAVAILABLE", "Hosted sandbox has not been qualified", 503)
    run = db.scalar(
        select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id).with_for_update()
    )
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    answer = input_text.strip()
    if not 5 <= len(answer) <= 4000:
        raise ServiceError("INPUT_REQUIRED", "A bounded answer is required", 400)
    input_hash = canonical_hash({"actor": actor, "input_text": answer})
    if run.resume_key == idempotency_key and run.state != "PAUSED_INPUT":
        if run.resume_input_hash != input_hash:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for different input", 409)
        return run
    if run.state != "PAUSED_INPUT" or run.resume_target not in ACTIVE_STATES:
        raise ServiceError("RUN_NOT_RESUMABLE", "Run is not waiting for input", 409)
    if run.resume_key == idempotency_key:
        raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for a prior resume", 409)
    if run.cancel_requested or run.lease_owner or run.lease_until:
        raise ServiceError("RUN_NOT_RESUMABLE", "Run still has active ownership", 409)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active":
        raise ServiceError("TENANT_DISABLED", "Tenant is not active", 403)
    if tenant.policy_revision != run.config_snapshot.get("policy_version"):
        raise ServiceError("POLICY_REVIEW_REQUIRED", "Run policy changed", 409)
    uncertain = db.scalar(
        select(ToolAction.id)
        .where(
            ToolAction.tenant_id == tenant_id,
            ToolAction.run_id == run_id,
            ToolAction.status == "INTENDED",
        )
        .limit(1)
    )
    if uncertain is not None:
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Effect must be reconciled", 409)
    verify_pause_checkpoint(db, run)
    target = run.resume_target
    run.state = "QUEUED"
    run.resume_key = idempotency_key
    run.resume_input_hash = input_hash
    stale_dispatches = db.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.tenant_id == tenant_id,
            OutboxEvent.topic == "run.dispatch",
            OutboxEvent.status.in_(["pending", "processing"]),
            OutboxEvent.payload["run_id"].as_string() == run.id,
        )
        .with_for_update()
    ).all()
    for event in stale_dispatches:
        event.status = "delivered"
    append_event(
        db,
        run,
        "run.resumed",
        {
            "resume_target": target,
            "actor": actor,
            "input_text": answer,
        },
    )
    db.add(
        OutboxEvent(
            tenant_id=tenant_id,
            topic="run.dispatch",
            payload={"run_id": run.id, "resume_key": idempotency_key, **inject_trace()},
        )
    )
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor=actor,
            action="run.resume",
            target_ref=run.id,
            arguments_hash=canonical_hash(
                {
                    "run_id": run.id,
                    "input_text": answer,
                }
            ),
            policy_revision=tenant.policy_revision,
            outcome="allowed",
        )
    )
    return run


@tracer.start_as_current_span("run.resume_approval")
def resume_model_approval_run(
    db: Session,
    tenant_id: str,
    run_id: str,
    actor: str,
    reason: str,
    idempotency_key: str,
) -> Run:
    """Resume a fenced emergency pause after fresh qualification and owner approval."""
    set_safe_attributes(run_id=run_id, tenant_id=tenant_id)
    reason = reason.strip()
    if not 8 <= len(reason) <= 2000:
        raise ServiceError("APPROVAL_REASON_REQUIRED", "A bounded approval reason is required", 400)
    # Match emergency-disable and model reservation lock order.
    pinned = db.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id))
    if pinned is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    model = db.scalar(select(ModelEntry).where(
        ModelEntry.id == pinned.model_entry_id,
    ).with_for_update().execution_options(populate_existing=True))
    run = db.scalar(select(Run).where(
        Run.id == run_id, Run.tenant_id == tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    if model is None or run is None or model.id != run.model_entry_id:
        raise ServiceError("MODEL_UNAVAILABLE", "Pinned model is unavailable", 409)
    approval_hash = canonical_hash({"actor": actor, "reason": reason, "kind": "model_resume"})
    if run.resume_key == idempotency_key and run.state != "PAUSED_APPROVAL":
        if run.resume_input_hash != approval_hash:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for another approval", 409)
        return run
    if run.state != "PAUSED_APPROVAL" or run.resume_target not in (
        ACTIVE_STATES | {"QUEUED", "REVIEW_READY"}
    ):
        raise ServiceError("RUN_NOT_RESUMABLE", "Run is not waiting for model approval", 409)
    if run.resume_key == idempotency_key:
        raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for a prior resume", 409)
    if run.cancel_requested or run.lease_owner or run.lease_until:
        raise ServiceError("RUN_NOT_RESUMABLE", "Run still has active ownership", 409)
    required = db.scalar(select(RunEvent).where(
        RunEvent.tenant_id == tenant_id,
        RunEvent.run_id == run_id,
        RunEvent.event_type == "approval.required",
    ).order_by(RunEvent.sequence.desc()).limit(1))
    if required is None or (required.payload or {}).get("kind") != "model_emergency_disable":
        raise ServiceError("APPROVAL_UNAVAILABLE", "No model resume approval is pending", 409)
    if aware(required.created_at) + timedelta(hours=24) <= utcnow():
        raise ServiceError("APPROVAL_EXPIRED", "Model resume approval expired", 409)
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "active":
        raise ServiceError("TENANT_DISABLED", "Tenant is not active", 403)
    snapshot = run.config_snapshot or {}
    if tenant.policy_revision != snapshot.get("policy_version"):
        raise ServiceError("POLICY_REVIEW_REQUIRED", "Run policy changed", 409)
    if (
        snapshot.get("model_registry_revision") != model.registry_revision
        or snapshot.get("model_price_revision") != model.price_revision
        or snapshot.get("model_context_limit") != model.context_limit
        or snapshot.get("model_output_limit") != model.output_limit
        or snapshot.get("model_price_per_m_input") != str(model.price_per_m_input)
        or snapshot.get("model_price_per_m_output") != str(model.price_per_m_output)
        or snapshot.get("model_price_per_m_cache_read") != (
            str(model.price_per_m_cache_read)
            if model.price_per_m_cache_read is not None else None
        )
        or snapshot.get("model_price_per_m_cache_write") != (
            str(model.price_per_m_cache_write)
            if model.price_per_m_cache_write is not None else None
        )
        or not qualification_for_pinned_run(model)
    ):
        raise ServiceError(
            "MODEL_QUALIFICATION_REQUIRED", "Pinned model needs fresh qualification", 409
        )
    if snapshot.get("execution_profile") == "hosted_vm_v1" and not (
        settings().environment != "development" and settings().hosted_execution_enabled
    ):
        raise ServiceError("EXECUTION_UNAVAILABLE", "Hosted execution is disabled", 503)
    uncertain = db.scalar(select(ToolAction.id).where(
        ToolAction.tenant_id == tenant_id,
        ToolAction.run_id == run_id,
        ToolAction.status == "INTENDED",
    ).limit(1))
    if uncertain is not None:
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Effect must be reconciled", 409)
    verify_pause_checkpoint(db, run)
    target = run.resume_target
    run.state = "REVIEW_READY" if target == "REVIEW_READY" else "QUEUED"
    run.resume_key = idempotency_key
    run.resume_input_hash = approval_hash
    append_event(db, run, "approval.granted", {
        "kind": "model_emergency_disable", "actor": actor,
        "required_event_id": required.id,
    })
    append_event(db, run, "run.resumed", {"resume_target": target, "actor": actor})
    if run.state == "QUEUED":
        stale_dispatches = db.scalars(select(OutboxEvent).where(
            OutboxEvent.tenant_id == tenant_id,
            OutboxEvent.topic == "run.dispatch",
            OutboxEvent.status.in_(["pending", "processing"]),
            OutboxEvent.payload["run_id"].as_string() == run.id,
        ).with_for_update()).all()
        for event in stale_dispatches:
            event.status = "delivered"
        db.add(OutboxEvent(
            tenant_id=tenant_id, topic="run.dispatch",
            payload={"run_id": run.id, "resume_key": idempotency_key, **inject_trace()},
        ))
    db.add(AuditEvent(
        tenant_id=tenant_id, actor=actor, action="run.approval_resume",
        target_ref=run.id,
        arguments_hash=canonical_hash({"run_id": run.id, "reason": reason}),
        policy_revision=tenant.policy_revision, outcome="allowed",
    ))
    return run


def expire_model_approvals(
    db: Session, tenant_id: str, *, now: datetime | None = None, limit: int = 100
) -> int:
    """Close emergency pauses whose 24-hour owner approval window elapsed."""
    now = now or utcnow()
    runs = db.scalars(select(Run).where(
        Run.tenant_id == tenant_id,
        Run.state == "PAUSED_APPROVAL",
    ).order_by(Run.updated_at, Run.id).with_for_update(skip_locked=True).limit(limit)).all()
    expired = 0
    for run in runs:
        required = db.scalar(select(RunEvent).where(
            RunEvent.tenant_id == tenant_id,
            RunEvent.run_id == run.id,
            RunEvent.event_type == "approval.required",
        ).order_by(RunEvent.sequence.desc()).limit(1))
        if (
            required is None
            or (required.payload or {}).get("kind") != "model_emergency_disable"
            or aware(required.created_at) + timedelta(hours=24) > now
        ):
            continue
        run.state = "CANCELLED"
        run.verdict = "INCONCLUSIVE"
        run.cancel_requested = True
        run.lease_fence += 1
        run.lease_owner = None
        run.lease_until = None
        append_event(db, run, "approval.expired", {
            "kind": "model_emergency_disable", "required_event_id": required.id,
        })
        append_event(db, run, "run.state_changed", {
            "state": "CANCELLED", "verdict": run.verdict,
        })
        append_event(db, run, "run.closed", {
            "state": "CANCELLED", "verdict": run.verdict,
        })
        db.add(AuditEvent(
            tenant_id=tenant_id, actor="approval-expiry-worker",
            action="run.approval_expired", target_ref=run.id,
            arguments_hash=canonical_hash({"run_id": run.id, "required_event_id": required.id}),
            policy_revision=(run.config_snapshot or {}).get("policy_version", "unknown"),
            outcome="expired",
        ))
        expired += 1
    return expired


@tracer.start_as_current_span("run.resume_budget")
def resume_budget_run(
    db: Session,
    tenant_id: str,
    run_id: str,
    actor: str,
    reason: str,
    new_spend_limit_usd: Decimal,
    idempotency_key: str,
) -> Run:
    """Approve one bounded run budget increase and requeue the fenced run."""
    set_safe_attributes(run_id=run_id, tenant_id=tenant_id)
    reason = reason.strip()
    if not 8 <= len(reason) <= 2000:
        raise ServiceError("APPROVAL_REASON_REQUIRED", "A bounded approval reason is required", 400)
    if not new_spend_limit_usd.is_finite() or new_spend_limit_usd <= 0:
        raise ServiceError("INVALID_BUDGET", "A positive budget is required", 400)
    new_spend_limit_usd = new_spend_limit_usd.quantize(Decimal("0.000001"))
    pinned = db.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id))
    if pinned is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    model = db.scalar(select(ModelEntry).where(
        ModelEntry.id == pinned.model_entry_id,
    ).with_for_update().execution_options(populate_existing=True))
    run = db.scalar(select(Run).where(
        Run.id == run_id, Run.tenant_id == tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    if model is None or run is None or model.id != run.model_entry_id:
        raise ServiceError("MODEL_UNAVAILABLE", "Pinned model is unavailable", 409)
    approval_hash = canonical_hash({
        "actor": actor, "reason": reason,
        "new_spend_limit_usd": str(new_spend_limit_usd),
    })
    if run.resume_key == idempotency_key and run.state != "PAUSED_BUDGET":
        if run.resume_input_hash != approval_hash:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for another approval", 409)
        return run
    if run.state != "PAUSED_BUDGET" or run.resume_target not in {
        "INVESTIGATING", "PATCHING", "VERIFYING",
    }:
        raise ServiceError("RUN_NOT_RESUMABLE", "Run is not waiting for budget approval", 409)
    if run.resume_key == idempotency_key:
        raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for a prior resume", 409)
    if run.cancel_requested or run.lease_owner or run.lease_until:
        raise ServiceError("RUN_NOT_RESUMABLE", "Run still has active ownership", 409)
    pause = db.scalar(select(RunEvent).where(
        RunEvent.tenant_id == tenant_id,
        RunEvent.run_id == run_id,
        RunEvent.event_type == "budget.pause",
    ).order_by(RunEvent.sequence.desc()).limit(1))
    if pause is None:
        raise ServiceError("APPROVAL_UNAVAILABLE", "No budget pause is pending", 409)
    if aware(pause.created_at) + timedelta(hours=24) <= utcnow():
        raise ServiceError("APPROVAL_EXPIRED", "Budget approval window expired", 409)
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "active":
        raise ServiceError("TENANT_DISABLED", "Tenant is not active", 403)
    snapshot = run.config_snapshot or {}
    if tenant.policy_revision != snapshot.get("policy_version"):
        raise ServiceError("POLICY_REVIEW_REQUIRED", "Run policy changed", 409)
    controlled_fixture = bool(
        settings().environment == "development"
        and model.validated_at
        and (model.capabilities or {}).get("controlled_provider_fixture") is True
    )
    if not qualification_for_pinned_run(model) and not controlled_fixture:
        raise ServiceError("MODEL_QUALIFICATION_REQUIRED", "Pinned model is unavailable", 409)
    current_limit = Decimal(str(snapshot.get("spend_limit_usd", "0")))
    operator_cap = Decimal(str(settings().max_run_spend_usd))
    if (
        new_spend_limit_usd <= current_limit
        or new_spend_limit_usd > operator_cap
    ):
        raise ServiceError("RUN_BUDGET_CAP", "Increase exceeds the allowed budget", 409)
    if snapshot.get("execution_profile") == "hosted_vm_v1" and not (
        settings().environment != "development" and settings().hosted_execution_enabled
    ):
        raise ServiceError("EXECUTION_UNAVAILABLE", "Hosted execution is disabled", 503)
    uncertain = db.scalar(select(ToolAction.id).where(
        ToolAction.tenant_id == tenant_id,
        ToolAction.run_id == run_id,
        ToolAction.status == "INTENDED",
    ).limit(1))
    if uncertain is not None:
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Effect must be reconciled", 409)
    verify_pause_checkpoint(db, run)
    run_cap = db.scalar(select(BudgetEntry).where(
        BudgetEntry.tenant_id == tenant_id,
        BudgetEntry.run_id == run_id,
        BudgetEntry.category == "run_cap",
    ).with_for_update())
    if (
        run_cap is None or run_cap.status != "reserved"
        or Decimal(run_cap.reserved_usd) != current_limit
    ):
        raise ServiceError("LEDGER_INCOMPLETE", "Run cap reservation is unavailable", 409)
    target = run.resume_target
    run.config_snapshot = {**snapshot, "spend_limit_usd": str(new_spend_limit_usd)}
    run_cap.reserved_usd = new_spend_limit_usd
    run.state = "QUEUED"
    run.resume_key = idempotency_key
    run.resume_input_hash = approval_hash
    stale_dispatches = db.scalars(select(OutboxEvent).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == "run.dispatch",
        OutboxEvent.status.in_(["pending", "processing"]),
        OutboxEvent.payload["run_id"].as_string() == run.id,
    ).with_for_update()).all()
    for event in stale_dispatches:
        event.status = "delivered"
    append_event(db, run, "budget.approved", {
        "previous_limit_usd": str(current_limit),
        "new_limit_usd": str(new_spend_limit_usd),
        "actor": actor, "pause_event_id": pause.id,
    })
    append_event(db, run, "run.resumed", {"resume_target": target, "actor": actor})
    db.add(OutboxEvent(
        tenant_id=tenant_id, topic="run.dispatch",
        payload={"run_id": run.id, "resume_key": idempotency_key, **inject_trace()},
    ))
    db.add(AuditEvent(
        tenant_id=tenant_id, actor=actor, action="run.budget_resume",
        target_ref=run.id,
        arguments_hash=canonical_hash({
            "run_id": run.id, "reason": reason,
            "previous_limit_usd": str(current_limit),
            "new_limit_usd": str(new_spend_limit_usd),
        }),
        policy_revision=tenant.policy_revision, outcome="allowed",
    ))
    return run


def expire_budget_pauses(
    db: Session, tenant_id: str, *, now: datetime | None = None, limit: int = 100
) -> int:
    """Close unrevised run budgets after their 24-hour approval window."""
    now = now or utcnow()
    runs = db.scalars(select(Run).where(
        Run.tenant_id == tenant_id,
        Run.state == "PAUSED_BUDGET",
    ).order_by(Run.updated_at, Run.id).with_for_update(skip_locked=True).limit(limit)).all()
    expired = 0
    for run in runs:
        pause = db.scalar(select(RunEvent).where(
            RunEvent.tenant_id == tenant_id,
            RunEvent.run_id == run.id,
            RunEvent.event_type == "budget.pause",
        ).order_by(RunEvent.sequence.desc()).limit(1))
        if pause is None or aware(pause.created_at) + timedelta(hours=24) > now:
            continue
        run.state = "CANCELLED"
        run.verdict = "INCONCLUSIVE"
        run.cancel_requested = True
        run.lease_fence += 1
        run.lease_owner = None
        run.lease_until = None
        append_event(db, run, "budget.expired", {"pause_event_id": pause.id})
        append_event(db, run, "run.state_changed", {
            "state": "CANCELLED", "verdict": run.verdict,
        })
        append_event(db, run, "run.closed", {
            "state": "CANCELLED", "verdict": run.verdict,
        })
        db.add(AuditEvent(
            tenant_id=tenant_id, actor="approval-expiry-worker",
            action="run.budget_expired", target_ref=run.id,
            arguments_hash=canonical_hash({"run_id": run.id, "pause_event_id": pause.id}),
            policy_revision=(run.config_snapshot or {}).get("policy_version", "unknown"),
            outcome="expired",
        ))
        expired += 1
    return expired


def begin_tool_action(
    db: Session,
    run: Run,
    worker_id: str,
    fence: int,
    step_id: str,
    logical_action: str,
    arguments: dict,
    authorized: bool,
) -> ToolAction:
    assert_fence(run, worker_id, fence)
    if run.cancel_requested:
        raise ServiceError("RUN_CANCELLED", "Cancellation stops new tool actions", 409)
    effect_key = canonical_hash([run.tenant_id, run.id, step_id, logical_action])
    arguments_hash = canonical_hash(arguments)
    existing = db.scalar(select(ToolAction).where(ToolAction.effect_key == effect_key))
    if existing:
        if existing.arguments_hash != arguments_hash:
            raise ServiceError("EFFECT_CONFLICT", "Effect key arguments changed", 409)
        if existing.status == "COMPLETED":
            return existing
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Effect must be reconciled", 409)
    action = ToolAction(
        tenant_id=run.tenant_id,
        run_id=run.id,
        step_id=step_id,
        logical_action=logical_action,
        effect_key=effect_key,
        arguments_hash=arguments_hash,
        policy_result="allowed" if authorized else "denied",
        status="INTENDED" if authorized else "DENIED",
    )
    db.add(action)
    append_event(
        db,
        run,
        "tool.authorized",
        {
            "step_id": step_id,
            "action": logical_action,
            "authorized": authorized,
        },
    )
    return action


def complete_tool_action(
    db: Session, run: Run, worker_id: str, fence: int, action: ToolAction, receipt: dict
) -> None:
    assert_fence(run, worker_id, fence)
    if action.run_id != run.id or action.policy_result != "allowed":
        raise ServiceError("TOOL_DENIED", "Tool action is not authorized", 403)
    if action.status == "COMPLETED":
        return
    action.status = "COMPLETED"
    action.receipt = receipt
    action.completed_at = utcnow()
    append_event(
        db,
        run,
        "tool.completed",
        {
            "step_id": action.step_id,
            "action": action.logical_action,
            "status": receipt.get("status", "unknown"),
        },
    )
    # Compare only stable result fields. Durations and artifact paths may vary
    # between otherwise identical attempts and must not disguise a stuck loop.
    db.flush()
    recent = db.scalars(
        select(ToolAction)
        .where(ToolAction.run_id == run.id, ToolAction.status == "COMPLETED")
        .order_by(ToolAction.completed_at.desc(), ToolAction.id.desc())
        .limit(3)
    ).all()
    if len(recent) != 3 or run.state not in ACTIVE_STATES:
        return
    signatures = {(item.logical_action, item.arguments_hash) for item in recent}
    progress_keys = (
        "status", "exit_code", "output_sha256", "candidate_tree_sha256",
        "patch_sha256", "result_sha256", "tree_sha256",
    )
    results = {
        canonical_hash({key: (item.receipt or {}).get(key) for key in progress_keys})
        for item in recent
    }
    if len(signatures) == 1 and len(results) == 1:
        append_event(db, run, "run.loop_detected", {
            "action": action.logical_action,
            "signature_sha256": canonical_hash([action.logical_action, action.arguments_hash]),
            "repetitions": 3,
        })
        transition(db, run, worker_id, fence, "FAILED", "INCONCLUSIVE")
