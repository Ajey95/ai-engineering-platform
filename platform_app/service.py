import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.config import settings
from platform_app.db import utcnow
from platform_app.models import (
    AuditEvent,
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
)
from platform_app.schemas import EventRead, RunCreate, RunRead

TERMINAL_STATES = {"COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"}


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def require_project(db: Session, tenant_id: str, project_id: str) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    )
    if project is None:
        raise ServiceError("NOT_FOUND", "Project not found", 404)
    return project


def require_task(db: Session, tenant_id: str, task_id: str) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id))
    if task is None:
        raise ServiceError("NOT_FOUND", "Task not found", 404)
    return task


def require_run(db: Session, tenant_id: str, run_id: str) -> Run:
    run = db.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id))
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    return run


def append_event(db: Session, run: Run, event_type: str, payload: dict) -> RunEvent:
    run.last_sequence += 1
    run.updated_at = utcnow()
    event = RunEvent(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=run.last_sequence,
        event_type=event_type,
        payload=payload,
    )
    db.add(event)
    return event


def event_read(event: RunEvent) -> EventRead:
    return EventRead(
        event_id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        timestamp=event.created_at,
        trace_id=event.trace_id,
        payload=event.payload,
    )


def run_read(run: Run) -> RunRead:
    return RunRead(
        id=run.id,
        task_id=run.task_id,
        project_id=run.project_id,
        state=run.state,
        verdict=run.verdict,
        media_status=run.media_status,
        base_commit=run.base_commit,
        model_entry_id=run.model_entry_id,
        cancel_requested=run.cancel_requested,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def admit_run(
    db: Session, tenant_id: str, actor: str, task_id: str, key: str, body: RunCreate
) -> Run:
    task = require_task(db, tenant_id, task_id)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active":
        raise ServiceError("TENANT_DISABLED", "Tenant is not active", 403)
    request_hash = canonical_hash(body.model_dump(mode="json"))
    existing = db.scalar(
        select(Run).where(
            Run.tenant_id == tenant_id, Run.created_by == actor, Run.idempotency_key == key
        )
    )
    if existing:
        if existing.request_hash != request_hash or existing.task_id != task_id:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for a different request", 409)
        return existing
    model = db.get(ModelEntry, body.selected_model_entry)
    if model is None or model.state != "enabled":
        raise ServiceError("MODEL_UNAVAILABLE", "Selected model is not enabled", 409)
    if not all(
        (
            model.context_limit,
            model.output_limit,
            model.price_revision,
            model.price_per_m_input is not None,
            model.price_per_m_output is not None,
        )
    ):
        raise ServiceError(
            "MODEL_UNAVAILABLE", "Selected model has unverified limits or pricing", 409
        )
    project = require_project(db, tenant_id, task.project_id)
    capabilities = model.capabilities or {}
    live_qualified = bool(model.validated_at and capabilities.get("live_qualified"))
    fixture_only = (
        settings().environment == "development"
        and body.reproduction.get("fixture_case_id") == "form-submit-001"
        and project.environment_manifest.get("case_id") == "form-submit-001"
        and capabilities.get("database_fixture_only") is True
    )
    if not live_qualified and not fixture_only:
        raise ServiceError("MODEL_UNAVAILABLE", "Selected model is not live qualified", 409)
    if not project.repository_url or not project.test_url or not project.environment_manifest:
        raise ServiceError("ENVIRONMENT_UNAVAILABLE", "Project setup is incomplete", 409)
    policy = {
        "prd_version": "1.0",
        "workflow_version": "0.1.0",
        "policy_version": tenant.policy_revision,
        "model_registry_revision": model.registry_revision,
        "model_id": model.model_id,
        "model_provider": model.provider,
        "max_model_calls": settings().max_model_calls,
        "max_tool_calls": settings().max_tool_calls,
        "max_patch_attempts": settings().max_patch_attempts,
        "active_timeout_seconds": settings().active_timeout_seconds,
        "spend_limit_usd": settings().max_run_spend_usd,
        "reproduction": body.reproduction,
        "repository_url": project.repository_url,
        "test_url": project.test_url,
        "environment_manifest": project.environment_manifest,
    }
    run = Run(
        tenant_id=tenant_id,
        project_id=project.id,
        task_id=task.id,
        created_by=actor,
        idempotency_key=key,
        request_hash=request_hash,
        base_commit=body.base_commit.lower(),
        model_entry_id=model.id,
        state="QUEUED",
        config_snapshot=policy,
    )
    db.add(run)
    db.flush()
    db.add(
        BudgetEntry(
            tenant_id=tenant_id,
            run_id=run.id,
            category="run_cap",
            reserved_usd=Decimal(str(settings().max_run_spend_usd)),
        )
    )
    append_event(db, run, "run.admitted", {"state": "QUEUED"})
    db.add(OutboxEvent(tenant_id=tenant_id, topic="run.dispatch", payload={"run_id": run.id}))
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor=actor,
            action="run.admit",
            target_ref=run.id,
            arguments_hash=request_hash,
            policy_revision=tenant.policy_revision,
            outcome="allowed",
        )
    )
    return run


def request_cancel(db: Session, run: Run, actor: str) -> None:
    if run.state in TERMINAL_STATES:
        return
    run.cancel_requested = True
    lease_until = run.lease_until
    if lease_until is not None and lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=UTC)
    active_lease = bool(run.lease_owner and lease_until and lease_until > datetime.now(UTC))
    if run.state == "QUEUED" or run.state.startswith("PAUSED") or not active_lease:
        run.state = "CANCELLED"
        run.verdict = "NOT_RUN"
        run.lease_owner = None
        run.lease_until = None
        append_event(db, run, "run.closed", {"state": "CANCELLED", "verdict": "NOT_RUN"})
    else:
        run.state = "CANCEL_REQUESTED"
        append_event(db, run, "run.state_changed", {"state": "CANCEL_REQUESTED"})
    db.add(
        AuditEvent(
            tenant_id=run.tenant_id,
            actor=actor,
            action="run.cancel",
            target_ref=run.id,
            arguments_hash=canonical_hash({"run_id": run.id}),
            policy_revision=run.config_snapshot["policy_version"],
            outcome="allowed",
        )
    )
