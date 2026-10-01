import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from platform_app.config import settings
from platform_app.db import Base, SessionLocal, engine, session_scope
from platform_app.memory import delete_fact, scoped_lookup
from platform_app.models import (
    BudgetEntry,
    MemoryFact,
    ModelEntry,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.schemas import (
    ErrorBody,
    EventRead,
    ModelRegister,
    ProjectCreate,
    ProjectRead,
    RunCreate,
    RunRead,
    TaskCreate,
    TaskRead,
)
from platform_app.service import (
    ServiceError,
    admit_run,
    canonical_hash,
    event_read,
    request_cancel,
    require_project,
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


def principal(authorization: str | None = Header(default=None)) -> tuple[str, str]:
    configured = settings().dev_token
    if configured and authorization != f"Bearer {configured}":
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    if settings().environment != "development" and not configured:
        raise HTTPException(status_code=503, detail="Identity provider is not configured")
    return settings().dev_tenant, settings().dev_actor


def db_session():
    yield from session_scope()


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


@app.post("/v1/projects", response_model=ProjectRead, status_code=201)
def create_project(
    body: ProjectCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    tenant_id, _ = identity
    project = Project(
        tenant_id=tenant_id,
        name=body.name,
        repository_url=str(body.repository_url) if body.repository_url else None,
        test_url=str(body.test_url) if body.test_url else None,
        environment_manifest=body.environment_manifest,
    )
    db.add(project)
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
    rows = db.scalars(select(Project).where(Project.tenant_id == identity[0]).limit(100)).all()
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


@app.post("/v1/tasks", response_model=TaskRead, status_code=201)
def create_task(
    body: TaskCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    tenant_id, actor = identity
    require_project(db, tenant_id, body.project_id)
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
        require_project(db, identity[0], project_id)
        query = query.where(Task.project_id == project_id)
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
        require_project(db, identity[0], project_id)
        query = query.where(Run.project_id == project_id)
    rows = db.scalars(query.order_by(Run.created_at.desc()).limit(100)).all()
    return [run_read(row) for row in rows]


@app.get("/v1/runs/{run_id}", response_model=RunRead)
def get_run(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    return run_read(require_run(db, identity[0], run_id))


@app.post("/v1/runs/{run_id}/cancel", response_model=RunRead, status_code=202)
def cancel_run(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = require_run(db, identity[0], run_id)
    if run.created_by != identity[1]:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    request_cancel(db, run, identity[1])
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
    require_run(db, identity[0], run_id)
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
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_run(db, identity[0], run_id)
    try:
        cursor = max(0, int(last_event_id or "0"))
    except ValueError as error:
        raise ServiceError("INVALID_CURSOR", "Last-Event-ID must be a sequence", 400) from error

    async def stream():
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            with SessionLocal() as read_db:
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
    run = require_run(db, identity[0], run_id)
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
        "qualification_scope": "synthetic_baseline_only" if receipts else "none",
        "autonomous_repair": False,
        "diagnosis_evidence_refs": [],
        "patch_hash": None,
        "changed_files": [],
        "baseline_tests": [baseline_named] if baseline_named is not None else [],
        "baseline_browser": baseline_browser,
        "baseline_oracle": baseline_oracle,
        "new_tests": [],
        "patched_tests": [],
        "browser_evidence_refs": [],
        "verification_status": run.verdict,
        "limitations": (
            ["No candidate patch or patched verification has executed"]
            if receipts
            else ["Execution has not produced verified evidence"]
        ),
        "media_status": run.media_status,
        "config_snapshot": run.config_snapshot,
    }


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
    rows = db.scalars(
        select(BudgetEntry)
        .where(BudgetEntry.tenant_id == identity[0])
        .order_by(BudgetEntry.created_at.desc())
        .limit(200)
    ).all()
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
    require_project(db, identity[0], project_id)
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
    if settings().environment != "development":
        raise ServiceError("TOOL_DENIED", "Project maintainer role is required", 403)
    require_project(db, identity[0], project_id)
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
