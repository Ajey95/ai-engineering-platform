import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePosixPath

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from platform_app.config import settings
from platform_app.db import utcnow
from platform_app.environment_manifest import EnvironmentManifest
from platform_app.model_qualification import qualification_current
from platform_app.model_routing import (
    RoutingError,
    select_qualified_model,
    validate_failover_routes,
)
from platform_app.models import (
    AuditEvent,
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Project,
    RepositoryConnection,
    Run,
    RunEvent,
    Task,
)
from platform_app.schemas import EventRead, RunCreate, RunRead
from platform_app.telemetry import inject_trace, set_safe_attributes, tracer
from platform_app.tenant_quota import QuotaError, check_admission_quota, lock_tenant

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
    if db.get_bind().dialect.name == "postgresql":
        # NOTIFY is delivered only after the surrounding transaction commits.
        # The SSE stream always reads the durable row before forwarding it.
        db.execute(text("SELECT pg_notify('aip_run_events', :run_id)"), {"run_id": run.id})
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
        spend_limit_usd=str(run.config_snapshot.get("spend_limit_usd", "0")),
        cancel_requested=run.cancel_requested,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@tracer.start_as_current_span("run.admit")
def admit_run(
    db: Session, tenant_id: str, actor: str, task_id: str, key: str, body: RunCreate
) -> Run:
    set_safe_attributes(tenant_id=tenant_id, task_id=task_id)
    operator_cap = Decimal(str(settings().max_run_spend_usd))
    run_spend_limit = body.max_spend_usd or operator_cap
    hosted_requested = body.reproduction.get("execution_profile") == "hosted_vm_v1"
    hosted_enabled = settings().environment != "development" and (
        settings().hosted_execution_enabled
    )
    if hosted_requested and not hosted_enabled:
        raise ServiceError("EXECUTION_UNAVAILABLE", "Hosted sandbox has not been qualified", 503)
    if settings().environment != "development" and not hosted_requested:
        raise ServiceError("EXECUTION_UNAVAILABLE", "Hosted sandbox has not been qualified", 503)
    task = require_task(db, tenant_id, task_id)
    try:
        tenant = lock_tenant(db, tenant_id)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 403) from error
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
    if run_spend_limit > operator_cap:
        raise ServiceError("RUN_BUDGET_CAP", "Run budget exceeds the operator cap", 409)
    project = require_project(db, tenant_id, task.project_id)
    routing_decision = None
    if body.selected_model_entry == "auto":
        try:
            routing_decision = select_qualified_model(db, tenant, project)
        except RoutingError as error:
            raise ServiceError(error.code, str(error), 409) from error
        model = routing_decision.model
    else:
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
    capabilities = model.capabilities or {}
    live_qualified = qualification_current(model)
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
    repair_paths = None
    if hosted_requested:
        from platform_app.repository_connections import (
            environment_secret_name,
            github_repository_ref,
        )

        try:
            manifest = EnvironmentManifest.model_validate(project.environment_manifest)
            repository_ref = github_repository_ref(project.repository_url)
        except (ValueError, ServiceError) as error:
            raise ServiceError(
                "ENVIRONMENT_UNAVAILABLE", "Hosted project setup is invalid", 409
            ) from error
        if manifest.external_destinations or manifest.environment_keys or manifest.postgres_fixture:
            raise ServiceError(
                "ENVIRONMENT_UNAVAILABLE", "Requested guest capability is not qualified", 409
            )
        repair_paths = body.reproduction.get("repair_paths")
        if (
            not isinstance(repair_paths, list) or not 1 <= len(repair_paths) <= 4
            or any(
                not isinstance(path, str) or not path or len(path) > 300
                or path.startswith("/") or "\\" in path or ":" in path
                or ".." in PurePosixPath(path).parts
                or path != PurePosixPath(path).as_posix()
                for path in repair_paths
            ) or len(set(repair_paths)) != len(repair_paths)
        ):
            raise ServiceError("REPAIR_SCOPE_INVALID", "Repair paths must be bounded", 409)
        connection = db.scalar(select(RepositoryConnection).where(
            RepositoryConnection.tenant_id == tenant_id,
            RepositoryConnection.project_id == project.id,
            RepositoryConnection.provider == "github",
            RepositoryConnection.repository_ref == repository_ref,
            RepositoryConnection.status == "ready",
        ))
        if connection is None or not connection.credential_ref:
            raise ServiceError(
                "REPOSITORY_UNAVAILABLE", "Ready repository connection is required", 409
            )
        try:
            environment_secret_name(connection.credential_ref)
        except ServiceError as error:
            raise ServiceError(
                "REPOSITORY_UNAVAILABLE", "Repository secret reference is unsupported", 409
            ) from error
    try:
        check_admission_quota(db, tenant)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 409) from error
    policy = {
        "prd_version": "1.0",
        "workflow_version": "0.1.0",
        "policy_version": tenant.policy_revision,
        "model_registry_revision": model.registry_revision,
        "model_price_revision": model.price_revision,
        "model_context_limit": model.context_limit,
        "model_output_limit": model.output_limit,
        "model_price_per_m_input": str(model.price_per_m_input),
        "model_price_per_m_output": str(model.price_per_m_output),
        "model_id": model.model_id,
        "model_provider": model.provider,
        "max_model_calls": settings().max_model_calls,
        "max_tool_calls": settings().max_tool_calls,
        "max_patch_attempts": settings().max_patch_attempts,
        "active_timeout_seconds": settings().active_timeout_seconds,
        "spend_limit_usd": str(run_spend_limit),
        "reproduction": body.reproduction,
        "repository_url": project.repository_url,
        "test_url": project.test_url,
        "environment_manifest": project.environment_manifest,
    }
    if hosted_requested:
        policy["execution_profile"] = "hosted_vm_v1"
        policy["repair_paths"] = repair_paths
        policy["repository_connection_id"] = connection.id
    try:
        policy["model_failover_routes"] = [
            route for route in validate_failover_routes(tenant.model_routing_policy or {})
            if route["from_model_entry_id"] == model.id
        ]
    except RoutingError as error:
        raise ServiceError(error.code, str(error), 409) from error
    if routing_decision is not None:
        policy["model_route"] = {
            "mode": "automatic",
            "policy_revision": routing_decision.policy_revision,
            "evidence_id": routing_decision.evidence.id,
            "suite_revision": routing_decision.evidence.suite_revision,
            "source_sha256": routing_decision.evidence.source_sha256,
            "score": str(routing_decision.score),
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
            reserved_usd=run_spend_limit,
        )
    )
    append_event(db, run, "run.admitted", {"state": "QUEUED"})
    if routing_decision is not None:
        append_event(
            db, run, "model.routed",
            {
                "model_entry_id": model.id,
                "evidence_id": routing_decision.evidence.id,
                "policy_revision": routing_decision.policy_revision,
            },
        )
    db.add(
        OutboxEvent(
            tenant_id=tenant_id,
            topic="run.dispatch",
            payload={"run_id": run.id, **inject_trace()},
        )
    )
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
    from platform_app.models import SandboxLease

    locked = db.scalar(
        select(Run).where(Run.id == run.id, Run.tenant_id == run.tenant_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if locked is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    run = locked
    if run.state in TERMINAL_STATES:
        return
    run.cancel_requested = True
    lease_until = run.lease_until
    if lease_until is not None and lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=UTC)
    active_lease = bool(run.lease_owner and lease_until and lease_until > datetime.now(UTC))
    if run.state == "QUEUED" or run.state.startswith("PAUSED") or not active_lease:
        preserve_verdict = run.state == "REVIEW_READY"
        run.state = "CANCELLED"
        if not preserve_verdict:
            run.verdict = "NOT_RUN"
        run.lease_owner = None
        run.lease_until = None
        append_event(db, run, "run.closed", {"state": "CANCELLED", "verdict": run.verdict})
    else:
        run.state = "CANCEL_REQUESTED"
        append_event(db, run, "run.state_changed", {"state": "CANCEL_REQUESTED"})
    active_sandboxes = db.scalars(
        select(SandboxLease).where(
            SandboxLease.tenant_id == run.tenant_id,
            SandboxLease.run_id == run.id,
            SandboxLease.state.in_(["intended", "bootstrapping", "provisioned"]),
        ).with_for_update()
    ).all()
    for sandbox in active_sandboxes:
        sandbox.state = "revoked"
        sandbox.updated_at = utcnow()
        db.add(OutboxEvent(
            tenant_id=run.tenant_id,
            topic="sandbox.cleanup",
            payload={"run_id": run.id, "sandbox_lease_id": sandbox.id},
        ))
        append_event(db, run, "sandbox.revoked", {"sandbox_lease_id": sandbox.id})
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


def record_review_decision(
    db: Session,
    tenant_id: str,
    run_id: str,
    actor: str,
    decision: str,
    reason: str = "",
    reviewer_authorized: bool = False,
) -> Run:
    run = db.scalar(
        select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id).with_for_update()
    )
    if run is None or (run.created_by != actor and not reviewer_authorized):
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    if decision not in {"accepted", "rejected"}:
        raise ServiceError("INVALID_DECISION", "Unknown reviewer decision", 400)
    reason = reason.strip()
    if decision == "rejected" and len(reason) < 5:
        raise ServiceError("REASON_REQUIRED", "Rejection needs a reason", 400)
    prior = db.scalar(
        select(RunEvent)
        .where(RunEvent.run_id == run.id, RunEvent.event_type == "review.decision")
        .order_by(RunEvent.sequence.desc())
        .limit(1)
    )
    if run.state == "COMPLETED" and prior is not None:
        if prior.payload.get("decision") == decision and prior.payload.get("reason") == reason:
            return run
        raise ServiceError("REVIEW_CLOSED", "Review decision has already been recorded", 409)
    if run.state != "REVIEW_READY" or run.verdict != "PASSED":
        raise ServiceError("REVIEW_NOT_READY", "Only verified review-ready runs can be closed", 409)
    decision_event = append_event(
        db,
        run,
        "review.decision",
        {
            "decision": decision,
            "reason": reason,
            "actor": actor,
        },
    )
    db.flush()
    from platform_app.memory import propose_fact, verify_fact

    project = require_project(db, tenant_id, run.project_id)
    evidence_ref = f"run-event:{decision_event.id}"
    fact = propose_fact(
        db, tenant_id, run.project_id,
        run.config_snapshot.get("repository_ref") or project.repository_url
        or f"project:{project.id}",
        run.base_commit, "decision", f"Run {run.id} review decision",
        f"Reviewer {decision} the repair proposal."
        + (f" Reason: {reason}" if reason else ""),
        [evidence_ref], actor=actor,
    )
    verify_fact(
        db, fact, evidence_ref,
        "Human review decision only; not independent repair validation", actor,
    )
    run.state = "COMPLETED"
    append_event(db, run, "run.state_changed", {"state": "COMPLETED", "verdict": run.verdict})
    append_event(db, run, "run.closed", {"state": "COMPLETED", "verdict": run.verdict})
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor=actor,
            action="review.decide",
            target_ref=run.id,
            arguments_hash=canonical_hash({"decision": decision, "reason": reason}),
            policy_revision=run.config_snapshot.get("policy_version", "1.0"),
            outcome="allowed",
        )
    )
    return run
