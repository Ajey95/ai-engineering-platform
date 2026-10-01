"""Run ownership and side effect ledger. See FR-HAR-04 and AC-05/06."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import Run, ToolAction
from platform_app.service import ServiceError, append_event, canonical_hash

ACTIVE_STATES = {"PREPARING", "REPRODUCING", "INVESTIGATING", "PATCHING", "VERIFYING"}
TERMINAL_STATES = {"COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"}


def aware(value: datetime) -> datetime:
    # SQLite development tests return naive datetimes despite timezone=True.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


ALLOWED_TRANSITIONS = {
    "QUEUED": {"PREPARING", "CANCELLED"},
    "PREPARING": {"REPRODUCING", "FAILED", "PAUSED_INPUT", "CANCEL_REQUESTED"},
    "REPRODUCING": {"INVESTIGATING", "INCONCLUSIVE", "FAILED", "PAUSED_INPUT", "CANCEL_REQUESTED"},
    "INVESTIGATING": {"PATCHING", "INCONCLUSIVE", "FAILED", "PAUSED_INPUT", "CANCEL_REQUESTED"},
    "PATCHING": {"VERIFYING", "FAILED", "PAUSED_BUDGET", "CANCEL_REQUESTED"},
    "VERIFYING": {"PATCHING", "REVIEW_READY", "FAILED", "CANCEL_REQUESTED"},
    "REVIEW_READY": {"PATCHING", "COMPLETED", "CANCEL_REQUESTED"},
    "PAUSED_INPUT": {"QUEUED", "CANCELLED"},
    "PAUSED_BUDGET": {"QUEUED", "CANCELLED"},
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
    if next_state not in ALLOWED_TRANSITIONS.get(run.state, set()):
        raise ServiceError("INVALID_TRANSITION", f"Cannot move {run.state} to {next_state}", 409)
    run.state = next_state
    if verdict is not None:
        if verdict not in {"PASSED", "FAILED", "INCONCLUSIVE", "NOT_RUN"}:
            raise ServiceError("INVALID_VERDICT", "Unknown verification verdict", 400)
        run.verdict = verdict
    append_event(db, run, "run.state_changed", {"state": next_state, "verdict": run.verdict})
    if next_state in TERMINAL_STATES:
        append_event(db, run, "run.closed", {"state": next_state, "verdict": run.verdict})
    if (
        next_state in TERMINAL_STATES
        or next_state.startswith("PAUSED")
        or next_state == "REVIEW_READY"
    ):
        run.lease_owner = None
        run.lease_until = None


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
