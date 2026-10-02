import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from platform_app.auth import (
    READ_ROLES,
    REVIEW_ROLES,
    WRITE_ROLES,
    is_owner,
    require_owner,
    require_project_role,
    require_tenant_membership,
    verify_bearer,
    visible_project_ids,
)
from platform_app.config import settings
from platform_app.db import Base, SessionLocal, engine, session_scope
from platform_app.memory import delete_fact, scoped_lookup
from platform_app.models import (
    AuditEvent,
    BudgetEntry,
    MemoryFact,
    ModelEntry,
    Project,
    ProjectMembership,
    Run,
    RunEvent,
    Task,
    Tenant,
    TenantMembership,
    ToolAction,
)
from platform_app.review_patch import verified_fixture_diff
from platform_app.schemas import (
    ErrorBody,
    EventRead,
    ModelRegister,
    ProjectCreate,
    ProjectMembershipSet,
    ProjectRead,
    ReviewDecisionCreate,
    RunCreate,
    RunRead,
    TaskCreate,
    TaskRead,
    TenantMembershipSet,
)
from platform_app.service import (
    ServiceError,
    admit_run,
    canonical_hash,
    event_read,
    record_review_decision,
    request_cancel,
    require_run,
    require_task,
    run_read,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Development bootstrap is isolated from hosted migration and identity setup.
    if settings().environment == "development":
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            if db.get(Tenant, settings().dev_tenant) is None:
                db.add(Tenant(id=settings().dev_tenant, name="Local development"))
                db.commit()
    yield


app = FastAPI(title="AI Engineering Platform API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=ErrorBody(
            code=exc.code, message=exc.message, request_id=str(request.state.request_id)
        ).model_dump(),
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = uuid4()
    if settings().environment == "development" and not settings().dev_token:
        client_host = request.client.host if request.client else ""
        if client_host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse(
                status_code=403,
                content=ErrorBody(
                    code="LOCAL_ONLY",
                    message="Unauthenticated development API is local only",
                    request_id=str(request.state.request_id),
                ).model_dump(),
            )
    response = await call_next(request)
    response.headers["X-Request-ID"] = str(request.state.request_id)
    response.headers["Cache-Control"] = "no-store"
    return response


def db_session():
    yield from session_scope()


def principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    db: Session = Depends(db_session),
) -> tuple[str, str]:
    config = settings()
    if config.environment == "development":
        if config.dev_token and authorization != f"Bearer {config.dev_token}":
            raise ServiceError("UNAUTHENTICATED", "Invalid bearer token", 401)
        return config.dev_tenant, config.dev_actor
    if not authorization or not authorization.startswith("Bearer "):
        raise ServiceError("UNAUTHENTICATED", "Bearer token is required", 401)
    if not x_tenant_id or len(x_tenant_id) > 36:
        raise ServiceError("UNAUTHENTICATED", "Workspace selection is required", 401)
    subject = verify_bearer(authorization.removeprefix("Bearer "), config)
    require_tenant_membership(db, x_tenant_id, subject)
    return x_tenant_id, subject


def authorized_run(
    db: Session, identity: tuple[str, str], run_id: str,
    roles: frozenset[str] = READ_ROLES,
) -> Run:
    run = require_run(db, identity[0], run_id)
    require_project_role(db, identity, run.project_id, roles)
    return run


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


def dev_evaluation_root() -> Path:
    config = settings()
    if config.environment != "development" or config.dev_token:
        raise HTTPException(status_code=404, detail="Local evaluation view is disabled")
    artifact_root = Path(config.artifact_dir).resolve()
    evaluation_root = Path(config.dev_evaluation_dir).resolve()
    if not evaluation_root.is_relative_to(artifact_root):
        raise HTTPException(status_code=500, detail="Evaluation directory is outside artifacts")
    return evaluation_root


@app.get("/v1/dev/evaluation")
def dev_evaluation():
    root = dev_evaluation_root()
    packet_path = root / "review-packet.json"
    if not packet_path.is_file():
        raise HTTPException(status_code=404, detail="No local evaluation is available")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    results = {}
    for label in ("baseline", "candidate"):
        side = packet[label]
        results[label] = {
            "named_test": side["named_test"]["status"],
            "browser": side["browser"]["status"],
            "oracle": side["oracle"]["status"],
            "screenshot_url": f"/v1/dev/evaluation/screenshots/{label}"
            if side["browser"].get("final_screenshot")
            else None,
            "media_manifest_url": None,
        }
        if packet["local_media"][label]["status"] == "READY":
            pointer_path = root / "private-media" / "fixture" / label / "media" / "ready.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            results[label]["media_manifest_url"] = (
                f"/v1/dev/evaluation/media/{label}/{pointer['master']}"
            )
    return {
        "case_id": packet["case_id"],
        "qualification_scope": packet["qualification_scope"],
        "autonomous_repair": packet["autonomous_repair"],
        "candidate_origin": packet["candidate_origin"],
        "verdict": packet["verdict"],
        "baseline_tree_sha256": packet["baseline_tree_sha256"],
        "candidate_tree_sha256": packet["candidate_tree_sha256"],
        "results": results,
    }


@app.get("/v1/dev/evaluation/screenshots/{label}")
def dev_evaluation_screenshot(label: str):
    if label not in {"baseline", "candidate"}:
        raise HTTPException(status_code=404)
    selected = dev_evaluation_root() / label / "final.png"
    if not selected.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(selected, media_type="image/png")


@app.get("/v1/dev/evaluation/media/{label}/{part:path}")
def dev_evaluation_media(label: str, part: str):
    if label not in {"baseline", "candidate"}:
        raise HTTPException(status_code=404)
    root = dev_evaluation_root() / "private-media" / "fixture" / label / "media"
    pointer_path = root / "ready.json"
    if not pointer_path.is_file():
        raise HTTPException(status_code=404)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    effect_key = pointer["effect_key"]
    if (
        not isinstance(effect_key, str)
        or len(effect_key) != 64
        or any(character not in "0123456789abcdef" for character in effect_key)
    ):
        raise HTTPException(status_code=404)
    selected = (root / part).resolve()
    if (
        not selected.is_relative_to((root / effect_key).resolve())
        or selected.suffix not in {".m3u8", ".mp4", ".m4s"}
        or not selected.is_file()
    ):
        raise HTTPException(status_code=404)
    media_type = {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".mp4": "video/mp4",
        ".m4s": "video/iso.segment",
    }[selected.suffix]
    return FileResponse(selected, media_type=media_type)


@app.get("/v1/runs/{run_id}/media/{label}/{part:path}")
def run_media(
    run_id: str,
    label: str,
    part: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = authorized_run(db, identity, run_id)
    if label not in {"baseline", "candidate"}:
        raise HTTPException(status_code=404)
    artifact_root = Path(settings().artifact_dir).resolve()
    root = (
        artifact_root
        / "private-media" / run.tenant_id / f"{run.id}_{label}" / "media"
    )
    pointer_path = root / "ready.json"
    if not pointer_path.is_file():
        raise HTTPException(status_code=404)
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        effect_key = pointer["effect_key"]
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=404) from None
    if not isinstance(effect_key, str) or re.fullmatch(r"[0-9a-f]{64}", effect_key) is None:
        raise HTTPException(status_code=404)
    effect_dir = root / effect_key
    if (
        effect_dir.is_symlink()
        or not root.resolve().is_relative_to(artifact_root)
        or not effect_dir.resolve().is_relative_to(artifact_root)
    ):
        raise HTTPException(status_code=404)
    selected = (root / part).resolve()
    if (
        not selected.is_relative_to(effect_dir.resolve())
        or selected.suffix not in {".m3u8", ".mp4", ".m4s"}
        or not selected.is_file()
    ):
        raise HTTPException(status_code=404)
    media_type = {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".mp4": "video/mp4",
        ".m4s": "video/iso.segment",
    }[selected.suffix]
    return FileResponse(selected, media_type=media_type)


@app.get("/v1/runs/{run_id}/screenshot/{label}")
def run_screenshot(
    run_id: str,
    label: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = authorized_run(db, identity, run_id)
    if label not in {"baseline", "candidate"}:
        raise HTTPException(status_code=404)
    step = "browser" if label == "baseline" else "candidate_browser"
    action = db.scalar(select(ToolAction).where(
        ToolAction.run_id == run.id,
        ToolAction.tenant_id == identity[0],
        ToolAction.step_id == step,
        ToolAction.status == "COMPLETED",
    ))
    receipt = action.receipt if action else None
    if not receipt or receipt.get("final_screenshot") != "final.png":
        raise HTTPException(status_code=404)
    digest = receipt.get("screenshot_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise HTTPException(status_code=404)
    relative = Path(run.id) / (
        "baseline" if label == "baseline" else "candidate/evidence"
    ) / "final.png"
    artifact_root = Path(settings().artifact_dir).resolve()
    selected = (artifact_root / relative).resolve()
    if (
        not selected.is_relative_to(artifact_root)
        or not selected.is_file()
        or hashlib.sha256(selected.read_bytes()).hexdigest() != digest
    ):
        raise HTTPException(status_code=404)
    return FileResponse(selected, media_type="image/png")


@app.post("/v1/projects", response_model=ProjectRead, status_code=201)
def create_project(
    body: ProjectCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    tenant_id, _ = identity
    require_owner(db, identity)
    project = Project(
        tenant_id=tenant_id,
        name=body.name,
        repository_url=str(body.repository_url) if body.repository_url else None,
        test_url=str(body.test_url) if body.test_url else None,
        environment_manifest=body.environment_manifest,
    )
    db.add(project)
    db.flush()
    if settings().environment != "development":
        db.add(ProjectMembership(
            tenant_id=tenant_id, project_id=project.id,
            subject=identity[1], role="maintainer",
        ))
    db.commit()
    db.refresh(project)
    return ProjectRead(
        id=project.id,
        name=project.name,
        repository_url=project.repository_url,
        test_url=project.test_url,
        created_at=project.created_at,
    )


@app.get("/v1/projects", response_model=list[ProjectRead])
def list_projects(
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    query = select(Project).where(Project.tenant_id == identity[0])
    visible = visible_project_ids(db, identity)
    if visible is not None:
        query = query.where(Project.id.in_(visible))
    rows = db.scalars(query.limit(100)).all()
    return [
        ProjectRead(
            id=p.id,
            name=p.name,
            repository_url=p.repository_url,
            test_url=p.test_url,
            created_at=p.created_at,
        )
        for p in rows
    ]


@app.get("/v1/memberships")
def list_tenant_memberships(
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_owner(db, identity)
    rows = db.scalars(select(TenantMembership).where(
        TenantMembership.tenant_id == identity[0]
    ).order_by(TenantMembership.subject).limit(500)).all()
    return [{"subject": row.subject, "role": row.role, "status": row.status} for row in rows]


@app.put("/v1/memberships/{subject}")
def set_tenant_membership(
    subject: str,
    body: TenantMembershipSet,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    if not 1 <= len(subject) <= 200:
        raise ServiceError("INVALID_SUBJECT", "Invalid identity subject", 400)
    tenant = db.scalar(select(Tenant).where(Tenant.id == identity[0]).with_for_update())
    if tenant is None:
        raise ServiceError("NOT_FOUND", "Workspace not found", 404)
    require_owner(db, identity)
    row = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == identity[0], TenantMembership.subject == subject
    ))
    if row is not None and row.role == "owner" and row.status == "active" and (
        body.role != "owner" or body.status != "active"
    ):
        owner_count = db.scalar(select(func.count()).select_from(TenantMembership).where(
            TenantMembership.tenant_id == identity[0],
            TenantMembership.role == "owner", TenantMembership.status == "active",
        ))
        if owner_count <= 1:
            raise ServiceError("LAST_OWNER", "The last owner cannot be disabled", 409)
    if row is None:
        row = TenantMembership(tenant_id=identity[0], subject=subject)
        db.add(row)
    row.role, row.status = body.role, body.status
    db.add(AuditEvent(
        tenant_id=identity[0], actor=identity[1], action="membership.tenant_set",
        target_ref=subject, arguments_hash=canonical_hash(body.model_dump()),
        policy_revision=tenant.policy_revision, outcome="allowed",
    ))
    db.commit()
    return {"subject": row.subject, "role": row.role, "status": row.status}


@app.get("/v1/projects/{project_id}/members")
def list_project_memberships(
    project_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_owner(db, identity)
    require_project_role(db, identity, project_id)
    rows = db.scalars(select(ProjectMembership).where(
        ProjectMembership.tenant_id == identity[0],
        ProjectMembership.project_id == project_id,
    ).order_by(ProjectMembership.subject).limit(500)).all()
    return [{"subject": row.subject, "role": row.role, "status": row.status} for row in rows]


@app.put("/v1/projects/{project_id}/members/{subject}")
def set_project_membership(
    project_id: str,
    subject: str,
    body: ProjectMembershipSet,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    if not 1 <= len(subject) <= 200:
        raise ServiceError("INVALID_SUBJECT", "Invalid identity subject", 400)
    tenant = db.scalar(select(Tenant).where(Tenant.id == identity[0]).with_for_update())
    if tenant is None:
        raise ServiceError("NOT_FOUND", "Workspace not found", 404)
    require_owner(db, identity)
    require_project_role(db, identity, project_id)
    if body.status == "active":
        require_tenant_membership(db, identity[0], subject)
    row = db.scalar(select(ProjectMembership).where(
        ProjectMembership.tenant_id == identity[0],
        ProjectMembership.project_id == project_id,
        ProjectMembership.subject == subject,
    ))
    if row is None:
        row = ProjectMembership(
            tenant_id=identity[0], project_id=project_id, subject=subject
        )
        db.add(row)
    row.role, row.status = body.role, body.status
    db.add(AuditEvent(
        tenant_id=identity[0], actor=identity[1], action="membership.project_set",
        target_ref=f"{project_id}:{subject}", arguments_hash=canonical_hash(body.model_dump()),
        policy_revision=tenant.policy_revision, outcome="allowed",
    ))
    db.commit()
    return {"subject": row.subject, "role": row.role, "status": row.status}


@app.post("/v1/tasks", response_model=TaskRead, status_code=201)
def create_task(
    body: TaskCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    tenant_id, actor = identity
    require_project_role(db, identity, body.project_id, WRITE_ROLES)
    task = Task(
        tenant_id=tenant_id,
        project_id=body.project_id,
        report=body.report,
        expected_behavior=body.expected_behavior,
        actual_behavior=body.actual_behavior,
        created_by=actor,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        report=task.report,
        expected_behavior=task.expected_behavior,
        actual_behavior=task.actual_behavior,
        created_at=task.created_at,
    )


@app.get("/v1/tasks", response_model=list[TaskRead])
def list_tasks(
    project_id: str | None = None,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    query = select(Task).where(Task.tenant_id == identity[0])
    if project_id:
        require_project_role(db, identity, project_id)
        query = query.where(Task.project_id == project_id)
    else:
        visible = visible_project_ids(db, identity)
        if visible is not None:
            query = query.where(Task.project_id.in_(visible))
    rows = db.scalars(query.order_by(Task.created_at.desc()).limit(100)).all()
    return [
        TaskRead(
            id=t.id,
            project_id=t.project_id,
            report=t.report,
            expected_behavior=t.expected_behavior,
            actual_behavior=t.actual_behavior,
            created_at=t.created_at,
        )
        for t in rows
    ]


@app.post("/v1/tasks/{task_id}/runs", response_model=RunRead, status_code=202)
def create_run(
    task_id: str,
    body: RunCreate,
    idempotency_key: str = Header(min_length=8, max_length=200),
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    task = require_task(db, identity[0], task_id)
    require_project_role(db, identity, task.project_id, WRITE_ROLES)
    try:
        run = admit_run(db, identity[0], identity[1], task_id, idempotency_key, body)
        db.commit()
    except IntegrityError:
        db.rollback()
        run = db.scalar(
            select(Run).where(
                Run.tenant_id == identity[0],
                Run.created_by == identity[1],
                Run.idempotency_key == idempotency_key,
            )
        )
        if run is None:
            raise
        expected_hash = canonical_hash(body.model_dump(mode="json"))
        if run.task_id != task_id or run.request_hash != expected_hash:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "Key was used for a different request", 409)
    db.refresh(run)
    return run_read(run)


@app.get("/v1/runs", response_model=list[RunRead])
def list_runs(
    project_id: str | None = None,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    query = select(Run).where(Run.tenant_id == identity[0])
    if project_id:
        require_project_role(db, identity, project_id)
        query = query.where(Run.project_id == project_id)
    else:
        visible = visible_project_ids(db, identity)
        if visible is not None:
            query = query.where(Run.project_id.in_(visible))
    rows = db.scalars(query.order_by(Run.created_at.desc()).limit(100)).all()
    return [run_read(row) for row in rows]


@app.get("/v1/runs/{run_id}", response_model=RunRead)
def get_run(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    return run_read(authorized_run(db, identity, run_id))


@app.post("/v1/runs/{run_id}/cancel", response_model=RunRead, status_code=202)
def cancel_run(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = authorized_run(db, identity, run_id)
    if run.created_by != identity[1]:
        require_project_role(db, identity, run.project_id, frozenset({"maintainer"}))
    request_cancel(db, run, identity[1])
    db.commit()
    db.refresh(run)
    return run_read(run)


@app.post("/v1/runs/{run_id}/review-decision", response_model=RunRead)
def review_decision(
    run_id: str,
    body: ReviewDecisionCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    authorized_run(db, identity, run_id, REVIEW_ROLES)
    run = record_review_decision(
        db, identity[0], run_id, identity[1], body.decision, body.reason,
        reviewer_authorized=True,
    )
    db.commit()
    db.refresh(run)
    return run_read(run)


@app.get("/v1/runs/{run_id}/events/history", response_model=list[EventRead])
def event_history(
    run_id: str,
    after: int = 0,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    authorized_run(db, identity, run_id)
    rows = db.scalars(
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.sequence > max(0, after))
        .order_by(RunEvent.sequence)
        .limit(200)
    ).all()
    return [event_read(row) for row in rows]


@app.get("/v1/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    authorized_run(db, identity, run_id)
    try:
        cursor = max(0, int(last_event_id or "0"))
    except ValueError as error:
        raise ServiceError("INVALID_CURSOR", "Last-Event-ID must be a sequence", 400) from error

    async def stream():
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            with SessionLocal() as read_db:
                try:
                    if settings().environment != "development":
                        if not authorization or not authorization.startswith("Bearer "):
                            break
                        if verify_bearer(authorization.removeprefix("Bearer ")) != identity[1]:
                            break
                    authorized_run(read_db, identity, run_id)
                except ServiceError:
                    break
                rows = read_db.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.tenant_id == identity[0],
                        RunEvent.sequence > cursor,
                    )
                    .order_by(RunEvent.sequence)
                    .limit(100)
                ).all()
                events = [event_read(row) for row in rows]
            if events:
                for event in events:
                    cursor = event.sequence
                    yield (
                        f"id: {event.sequence}\nevent: {event.event_type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                idle = 0
            else:
                await asyncio.sleep(1)
                idle += 1
                if idle >= 15:
                    yield ": heartbeat\n\n"
                    idle = 0

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/runs/{run_id}/review-packet")
def review_packet(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = authorized_run(db, identity, run_id)
    task = require_task(db, identity[0], run.task_id)
    actions = db.scalars(
        select(ToolAction)
        .where(ToolAction.run_id == run.id, ToolAction.tenant_id == identity[0])
        .order_by(ToolAction.created_at)
    ).all()
    receipts = {
        action.step_id: action.receipt
        for action in actions
        if action.status == "COMPLETED" and action.receipt is not None
    }
    baseline_named = receipts.get("named")
    baseline_browser = receipts.get("browser")
    baseline_oracle = receipts.get("oracle")
    candidate_patch = receipts.get("candidate_patch")
    candidate_named = receipts.get("candidate_named")
    candidate_browser = receipts.get("candidate_browser")
    candidate_oracle = receipts.get("candidate_oracle")
    model_completed = any(
        action.logical_action == "model.generate" and action.status == "COMPLETED"
        for action in actions
    )
    review_event = db.scalar(select(RunEvent).where(
        RunEvent.run_id == run.id, RunEvent.event_type == "review.decision"
    ).order_by(RunEvent.sequence.desc()).limit(1))
    provider_mode = (candidate_patch or {}).get("provider_mode")
    media_urls = {}
    for label in ("baseline", "candidate"):
        media_receipt = receipts.get(f"media_{label}") or {}
        master = media_receipt.get("master")
        if media_receipt.get("status") == "READY" and isinstance(master, str):
            parts = Path(master).parts
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{64}", parts[-2]):
                media_urls[label] = (
                    f"/v1/runs/{run.id}/media/{label}/{parts[-2]}/master.m3u8"
                )
    screenshot_urls = {
        label: f"/v1/runs/{run.id}/screenshot/{label}"
        for label, receipt in (
            ("baseline", baseline_browser), ("candidate", candidate_browser)
        )
        if receipt and receipt.get("final_screenshot") == "final.png"
        and isinstance(receipt.get("screenshot_sha256"), str)
    }
    spend_entries = db.scalars(
        select(BudgetEntry).where(
            BudgetEntry.run_id == run.id,
            BudgetEntry.category.like("call:%"),
        )
    ).all()
    reproduced = (
        baseline_named is not None
        and baseline_named.get("status") == "PASSED"
        and baseline_browser is not None
        and baseline_browser.get("status") == "FAILED"
        and baseline_oracle is not None
        and baseline_oracle.get("status") == "FAILED"
    )
    return {
        "schema_version": "1.0",
        "run_id": run.id,
        "base_commit": run.base_commit,
        "report": task.report,
        "expected_behavior": task.expected_behavior,
        "actual_behavior": task.actual_behavior,
        "reproduction_status": "REPRODUCED"
        if reproduced
        else ("INCONCLUSIVE" if receipts else "NOT_RUN"),
        "qualification_scope": (
            "synthetic_container_controlled_provider"
            if provider_mode == "controlled_test"
            else "synthetic_container_fixture"
            if candidate_patch
            else "synthetic_baseline_only"
            if receipts
            else "none"
        ),
        "autonomous_repair": bool(
            candidate_patch and model_completed and provider_mode == "native_api"
        ),
        "diagnosis_evidence_refs": [],
        "patch_hash": (candidate_patch or {}).get("patch_sha256"),
        "patch_url": f"/v1/runs/{run.id}/patch" if candidate_patch else None,
        "changed_files": (candidate_patch or {}).get("changed_files", []),
        "diagnosis_hypothesis": (candidate_patch or {}).get("diagnosis_hypothesis"),
        "baseline_tests": [baseline_named] if baseline_named is not None else [],
        "baseline_browser": baseline_browser,
        "baseline_oracle": baseline_oracle,
        "new_tests": [],
        "patched_tests": [candidate_named] if candidate_named is not None else [],
        "candidate_browser": candidate_browser,
        "candidate_oracle": candidate_oracle,
        "browser_evidence_refs": list(screenshot_urls.values()),
        "screenshot_urls": screenshot_urls,
        "verification_status": run.verdict,
        "review_decision": (review_event.payload or {}).get("decision") if review_event else None,
        "review_reason": (review_event.payload or {}).get("reason") if review_event else None,
        "publication_status": "DISABLED",
        "limitations": (
            ["Controlled provider response; this does not qualify a live autonomous repair"]
            if provider_mode == "controlled_test"
            else ["Candidate checks are limited to the recorded synthetic fixture"]
            if candidate_patch
            else ["No candidate patch or patched verification has executed"]
            if receipts
            else ["Execution has not produced verified evidence"]
        ),
        "actual_model_spend_usd": str(sum((entry.actual_usd for entry in spend_entries), start=0)),
        "media_status": run.media_status,
        "media_manifest_urls": media_urls,
        "media_manifest_url": media_urls.get("candidate") or media_urls.get("baseline"),
        "config_snapshot": run.config_snapshot,
    }


@app.get("/v1/runs/{run_id}/patch")
def download_patch(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    if settings().environment != "development":
        raise HTTPException(status_code=404)
    run = authorized_run(db, identity, run_id)
    action = db.scalar(select(ToolAction).where(
        ToolAction.run_id == run.id,
        ToolAction.tenant_id == identity[0],
        ToolAction.step_id == "candidate_patch",
        ToolAction.status == "COMPLETED",
    ))
    if action is None or action.receipt is None:
        raise ServiceError("PATCH_UNAVAILABLE", "No verified candidate patch exists", 404)
    diff = verified_fixture_diff(
        run, action.receipt, Path(settings().artifact_dir).resolve(),
        Path(__file__).resolve().parents[1],
    )
    return PlainTextResponse(
        diff,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="run-{run.id}.patch"'},
    )


@app.get("/v1/models")
def list_models(
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    rows = db.scalars(select(ModelEntry).order_by(ModelEntry.id)).all()
    return [
        {
            "id": m.id,
            "provider": m.provider,
            "model_id": m.model_id,
            "state": m.state,
            "qualified": bool(
                m.state == "enabled"
                and m.validated_at
                and (m.capabilities or {}).get("live_qualified")
            ),
            "fixture_only": bool((m.capabilities or {}).get("database_fixture_only")),
            "capabilities": m.capabilities,
            "context_limit": m.context_limit,
            "output_limit": m.output_limit,
        }
        for m in rows
    ]


@app.post("/v1/models", status_code=201)
def register_model(
    body: ModelRegister,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    if settings().environment != "development":
        raise ServiceError("TOOL_DENIED", "Model registration requires administrator identity", 403)
    if db.get(ModelEntry, body.id):
        raise ServiceError("MODEL_EXISTS", "Model entry already exists", 409)
    model = ModelEntry(**body.model_dump(), state="registered")
    db.add(model)
    db.commit()
    return {"id": model.id, "state": model.state}


@app.get("/v1/usage")
def usage(
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    query = select(BudgetEntry).join(Run, BudgetEntry.run_id == Run.id).where(
        BudgetEntry.tenant_id == identity[0], Run.tenant_id == identity[0]
    )
    if not is_owner(db, identity):
        visible = visible_project_ids(db, identity) or set()
        query = query.where(Run.project_id.in_(visible))
    rows = db.scalars(query.order_by(BudgetEntry.created_at.desc()).limit(200)).all()
    return {
        "entries": [
            {
                "run_id": row.run_id,
                "category": row.category,
                "reserved_usd": float(row.reserved_usd),
                "actual_usd": float(row.actual_usd),
                "status": row.status,
            }
            for row in rows
        ]
    }


@app.get("/v1/projects/{project_id}/memory")
def get_memory(
    project_id: str,
    source_revision: str,
    query: str = "",
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_project_role(db, identity, project_id)
    facts = scoped_lookup(db, identity[0], project_id, source_revision, query)
    return {
        "retrieval_mode": "canonical_degraded",
        "facts": [
            {
                "id": fact.id,
                "subject": fact.subject,
                "statement": fact.statement,
                "source_revision": fact.source_revision,
                "source_refs": fact.source_refs,
                "verification_scope": fact.verification_scope,
                "status": fact.status,
            }
            for fact in facts
        ],
    }


@app.delete("/v1/projects/{project_id}/memory/{fact_id}", status_code=202)
def remove_memory(
    project_id: str,
    fact_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_project_role(db, identity, project_id, frozenset({"maintainer"}))
    fact = db.scalar(
        select(MemoryFact).where(
            MemoryFact.id == fact_id,
            MemoryFact.tenant_id == identity[0],
            MemoryFact.project_id == project_id,
        )
    )
    if fact is None:
        raise ServiceError("NOT_FOUND", "Memory fact not found", 404)
    delete_fact(db, fact)
    db.commit()
    return {"fact_id": fact.id, "status": fact.status, "propagation": "pending"}
