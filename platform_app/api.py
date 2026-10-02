import asyncio
import hashlib
import json
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
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
from platform_app.evidence_bundle import BundleError, build_evidence_bundle
from platform_app.graph_memory import GraphUnavailable, MemgraphProjection, connected_lookup
from platform_app.memory import delete_fact, scoped_lookup
from platform_app.model_base import utcnow
from platform_app.model_qualification import (
    QualificationError,
    qualification_current,
    register_model_entry,
)
from platform_app.models import (
    AuditEvent,
    BudgetEntry,
    MemoryFact,
    ModelEntry,
    Project,
    ProjectMembership,
    PublicationApproval,
    RecordingDeletion,
    RepositoryConnection,
    Run,
    RunEvent,
    Task,
    Tenant,
    TenantMembership,
    ToolAction,
)
from platform_app.publication import approve_draft_pr
from platform_app.recording_deletion import (
    deletion_for,
    purge_local_recording,
    reconcile_local_recording_deletions,
)
from platform_app.repository_connections import github_repository_ref, validate_credential_ref
from platform_app.review_patch import verified_fixture_diff
from platform_app.run_ledger import resume_input_run
from platform_app.schemas import (
    ErrorBody,
    EventRead,
    ModelRegister,
    ProjectCreate,
    ProjectMembershipSet,
    ProjectRead,
    PublicationApprovalCreate,
    PublicationApprovalRead,
    RepositoryConnectionCreate,
    RepositoryConnectionRead,
    ResumeInputCreate,
    ReviewDecisionCreate,
    RunCreate,
    RunRead,
    TaskCreate,
    TaskRead,
    TenantMembershipSet,
)
from platform_app.service import (
    TERMINAL_STATES,
    ServiceError,
    admit_run,
    append_event,
    canonical_hash,
    event_read,
    record_review_decision,
    request_cancel,
    require_run,
    require_task,
    run_read,
)
from platform_app.telemetry import configure_telemetry, extract_trace, set_safe_attributes, tracer


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_telemetry()
    # Development bootstrap is isolated from hosted migration and identity setup.
    if settings().environment == "development":
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            if db.get(Tenant, settings().dev_tenant) is None:
                db.add(Tenant(id=settings().dev_tenant, name="Local development"))
                db.commit()
        reconcile_local_recording_deletions(SessionLocal, settings().artifact_dir)
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
    with tracer.start_as_current_span("http.server", context=extract_trace(dict(request.headers))):
        set_safe_attributes(http_method=request.method)
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
        set_safe_attributes(http_status=response.status_code)
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
    db: Session,
    identity: tuple[str, str],
    run_id: str,
    roles: frozenset[str] = READ_ROLES,
) -> Run:
    run = require_run(db, identity[0], run_id)
    require_project_role(db, identity, run.project_id, roles)
    return run


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/v1/dev/fixture-info")
def dev_fixture_info(identity: tuple[str, str] = Depends(principal)):
    if settings().environment != "development":
        raise HTTPException(status_code=404)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    commit = result.stdout.strip().lower()
    if result.returncode or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ServiceError("FIXTURE_UNAVAILABLE", "Local fixture revision is unavailable", 503)
    return {"case_id": "form-submit-001", "base_commit": commit}


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
    if deletion_for(db, run, label) is not None:
        raise HTTPException(status_code=404)
    artifact_root = Path(settings().artifact_dir).resolve()
    root = artifact_root / "private-media" / run.tenant_id / f"{run.id}_{label}" / "media"
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


@app.delete("/v1/runs/{run_id}/recordings/{label}")
def delete_run_recording(
    run_id: str,
    label: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    if label not in {"baseline", "candidate"}:
        raise HTTPException(status_code=404)
    run = authorized_run(db, identity, run_id)
    if not is_owner(db, identity):
        require_project_role(db, identity, run.project_id, REVIEW_ROLES)
    # A terminal run cannot republish the recording after access is revoked.
    run = db.scalar(
        select(Run).where(Run.id == run.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if run.state not in TERMINAL_STATES | {"REVIEW_READY"}:
        raise ServiceError("RUN_ACTIVE", "Recording deletion requires a closed run", 409)
    deletion = deletion_for(db, run, label)
    if deletion is None:
        deletion = RecordingDeletion(
            tenant_id=run.tenant_id,
            run_id=run.id,
            label=label,
            actor=identity[1],
            status="pending",
        )
        db.add(deletion)
        db.add(AuditEvent(
            tenant_id=run.tenant_id,
            actor=identity[1],
            action="recording.delete",
            target_ref=f"run:{run.id}:recording:{label}",
            arguments_hash=canonical_hash({"run_id": run.id, "label": label}),
            policy_revision=str((run.config_snapshot or {}).get("policy_revision", "1.0")),
            outcome="access_revoked",
        ))
        append_event(db, run, "artifact.deletion_requested", {"label": label})
        db.commit()
        # The revocation commit releases the first lock. Reacquire the run row
        # so only one request removes this run's files or appends its final event.
        run = db.scalar(
            select(Run).where(Run.id == run.id).with_for_update()
            .execution_options(populate_existing=True)
        )
    deletion = db.scalar(
        select(RecordingDeletion)
        .where(RecordingDeletion.id == deletion.id)
        .execution_options(populate_existing=True)
    )
    if deletion.status != "complete":
        try:
            purge_local_recording(db, run, label, settings().artifact_dir)
        except (OSError, ValueError) as error:
            deletion.status = "failed"
            db.commit()
            raise ServiceError(
                "DELETE_PENDING", "Recording access revoked; object cleanup pending", 503
            ) from error
        deletion.status = "complete"
        deletion.completed_at = utcnow()
        other = "candidate" if label == "baseline" else "baseline"
        run.media_status = "DELETED" if deletion_for(db, run, other) else "PARTIALLY_DELETED"
        append_event(db, run, "artifact.deleted", {"label": label})
        db.commit()
    return {"run_id": run.id, "label": label, "status": deletion.status}


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
    action = db.scalar(
        select(ToolAction).where(
            ToolAction.run_id == run.id,
            ToolAction.tenant_id == identity[0],
            ToolAction.step_id == step,
            ToolAction.status == "COMPLETED",
        )
    )
    receipt = action.receipt if action else None
    if not receipt or receipt.get("final_screenshot") != "final.png":
        raise HTTPException(status_code=404)
    digest = receipt.get("screenshot_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise HTTPException(status_code=404)
    relative = (
        Path(run.id) / ("baseline" if label == "baseline" else "candidate/evidence") / "final.png"
    )
    artifact_root = Path(settings().artifact_dir).resolve()
    selected = (artifact_root / relative).resolve()
    if (
        not selected.is_relative_to(artifact_root)
        or not selected.is_file()
        or hashlib.sha256(selected.read_bytes()).hexdigest() != digest
    ):
        raise HTTPException(status_code=404)
    return FileResponse(selected, media_type="image/png")


EVIDENCE_DIRS = {
    "named": "baseline",
    "browser": "baseline",
    "oracle": "baseline",
    "candidate_named": "candidate/evidence",
    "candidate_browser": "candidate/evidence",
    "candidate_oracle": "candidate/evidence",
}


@app.get("/v1/runs/{run_id}/evidence/{step_id}/output")
def run_evidence_output(
    run_id: str,
    step_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = authorized_run(db, identity, run_id)
    folder = EVIDENCE_DIRS.get(step_id)
    if folder is None:
        raise HTTPException(status_code=404)
    action = db.scalar(
        select(ToolAction).where(
            ToolAction.run_id == run.id,
            ToolAction.tenant_id == identity[0],
            ToolAction.step_id == step_id,
            ToolAction.logical_action == f"fixture.{step_id}",
            ToolAction.status == "COMPLETED",
        )
    )
    receipt = action.receipt if action else None
    filename = receipt.get("output_file") if receipt else None
    digest = receipt.get("output_sha256") if receipt else None
    if (
        not isinstance(filename, str)
        or filename in {".", ".."}
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", filename) is None
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise HTTPException(status_code=404)
    try:
        root = Path(settings().artifact_dir).resolve(strict=True)
        run_root = root / run.id
        selected_dir = run_root / folder
        selected = selected_dir / filename
        if (
            run_root.is_symlink()
            or (run_root / "candidate").is_symlink()
            or selected_dir.is_symlink()
            or selected.is_symlink()
        ):
            raise HTTPException(status_code=404)
        resolved = selected.resolve(strict=True)
        if (
            not selected_dir.resolve(strict=True).is_relative_to(run_root.resolve(strict=True))
            or not resolved.is_relative_to(selected_dir.resolve(strict=True))
            or not resolved.is_file()
        ):
            raise HTTPException(status_code=404)
        with resolved.open("rb") as content:
            if hashlib.file_digest(content, "sha256").hexdigest() != digest:
                raise HTTPException(status_code=404)
    except OSError as error:
        raise HTTPException(status_code=404) from error
    return FileResponse(
        resolved, media_type="application/octet-stream",
        filename=f"{step_id}-{filename}",
        content_disposition_type="attachment",
    )


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
        db.add(
            ProjectMembership(
                tenant_id=tenant_id,
                project_id=project.id,
                subject=identity[1],
                role="maintainer",
            )
        )
    db.commit()
    db.refresh(project)
    return ProjectRead(
        id=project.id,
        name=project.name,
        repository_url=project.repository_url,
        test_url=project.test_url,
        created_at=project.created_at,
        fixture_case_id=(
            "form-submit-001"
            if settings().environment == "development"
            and project.environment_manifest.get("case_id") == "form-submit-001"
            else None
        ),
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
            fixture_case_id=(
                "form-submit-001"
                if settings().environment == "development"
                and p.environment_manifest.get("case_id") == "form-submit-001"
                else None
            ),
        )
        for p in rows
    ]


def _repository_connection_read(row: RepositoryConnection) -> RepositoryConnectionRead:
    readiness = (
        "disabled" if row.status == "disabled" else
        "credential_required" if row.credential_ref is None else
        "ready" if row.status == "ready" else "verification_required"
    )
    return RepositoryConnectionRead(
        id=row.id, project_id=row.project_id, provider="github",
        repository_ref=row.repository_ref, status=row.status,
        readiness=readiness, created_at=row.created_at, checked_at=row.checked_at,
    )


@app.post(
    "/v1/projects/{project_id}/repository-connections",
    response_model=RepositoryConnectionRead, status_code=201,
)
def create_repository_connection(
    project_id: str,
    body: RepositoryConnectionCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    project = require_project_role(db, identity, project_id, frozenset({"maintainer"}))
    repository_ref = github_repository_ref(body.repository_url)
    credential_ref = validate_credential_ref(body.credential_ref)
    db.scalar(select(Project).where(
        Project.id == project.id, Project.tenant_id == identity[0]
    ).with_for_update())
    row = db.scalar(select(RepositoryConnection).where(
        RepositoryConnection.tenant_id == identity[0],
        RepositoryConnection.project_id == project_id,
        RepositoryConnection.provider == "github",
        RepositoryConnection.repository_ref == repository_ref,
    ))
    if row is not None and row.credential_ref == credential_ref and row.status != "disabled":
        return _repository_connection_read(row)
    if row is None:
        row = RepositoryConnection(
            tenant_id=identity[0], project_id=project_id, provider="github",
            repository_ref=repository_ref, created_by=identity[1],
        )
        db.add(row)
    row.credential_ref = credential_ref
    row.status = "unverified"
    row.checked_at = None
    tenant = db.get(Tenant, identity[0])
    db.add(AuditEvent(
        tenant_id=identity[0], actor=identity[1], action="repository.connection_set",
        target_ref=row.id, arguments_hash=canonical_hash({
            "repository_ref": repository_ref, "credential_ref": credential_ref,
        }), policy_revision=tenant.policy_revision, outcome="unverified",
    ))
    db.commit()
    db.refresh(row)
    return _repository_connection_read(row)


@app.get(
    "/v1/projects/{project_id}/repository-connections",
    response_model=list[RepositoryConnectionRead],
)
def list_repository_connections(
    project_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_project_role(db, identity, project_id)
    rows = db.scalars(select(RepositoryConnection).where(
        RepositoryConnection.tenant_id == identity[0],
        RepositoryConnection.project_id == project_id,
    ).order_by(RepositoryConnection.created_at).limit(100)).all()
    return [_repository_connection_read(row) for row in rows]


@app.delete(
    "/v1/projects/{project_id}/repository-connections/{connection_id}",
    response_model=RepositoryConnectionRead,
)
def disable_repository_connection(
    project_id: str,
    connection_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_project_role(db, identity, project_id, frozenset({"maintainer"}))
    row = db.scalar(select(RepositoryConnection).where(
        RepositoryConnection.id == connection_id,
        RepositoryConnection.tenant_id == identity[0],
        RepositoryConnection.project_id == project_id,
    ).with_for_update())
    if row is None:
        raise ServiceError("NOT_FOUND", "Repository connection not found", 404)
    if row.status != "disabled":
        row.status = "disabled"
        tenant = db.get(Tenant, identity[0])
        db.add(AuditEvent(
            tenant_id=identity[0], actor=identity[1], action="repository.connection_disable",
            target_ref=row.id, arguments_hash=canonical_hash({"id": row.id}),
            policy_revision=tenant.policy_revision, outcome="disabled",
        ))
        db.commit()
        db.refresh(row)
    return _repository_connection_read(row)


@app.get("/v1/memberships")
def list_tenant_memberships(
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    require_owner(db, identity)
    rows = db.scalars(
        select(TenantMembership)
        .where(TenantMembership.tenant_id == identity[0])
        .order_by(TenantMembership.subject)
        .limit(500)
    ).all()
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
    row = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == identity[0], TenantMembership.subject == subject
        )
    )
    if (
        row is not None
        and row.role == "owner"
        and row.status == "active"
        and (body.role != "owner" or body.status != "active")
    ):
        owner_count = db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.tenant_id == identity[0],
                TenantMembership.role == "owner",
                TenantMembership.status == "active",
            )
        )
        if owner_count <= 1:
            raise ServiceError("LAST_OWNER", "The last owner cannot be disabled", 409)
    if row is None:
        row = TenantMembership(tenant_id=identity[0], subject=subject)
        db.add(row)
    row.role, row.status = body.role, body.status
    db.add(
        AuditEvent(
            tenant_id=identity[0],
            actor=identity[1],
            action="membership.tenant_set",
            target_ref=subject,
            arguments_hash=canonical_hash(body.model_dump()),
            policy_revision=tenant.policy_revision,
            outcome="allowed",
        )
    )
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
    rows = db.scalars(
        select(ProjectMembership)
        .where(
            ProjectMembership.tenant_id == identity[0],
            ProjectMembership.project_id == project_id,
        )
        .order_by(ProjectMembership.subject)
        .limit(500)
    ).all()
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
    row = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.tenant_id == identity[0],
            ProjectMembership.project_id == project_id,
            ProjectMembership.subject == subject,
        )
    )
    if row is None:
        row = ProjectMembership(tenant_id=identity[0], project_id=project_id, subject=subject)
        db.add(row)
    row.role, row.status = body.role, body.status
    db.add(
        AuditEvent(
            tenant_id=identity[0],
            actor=identity[1],
            action="membership.project_set",
            target_ref=f"{project_id}:{subject}",
            arguments_hash=canonical_hash(body.model_dump()),
            policy_revision=tenant.policy_revision,
            outcome="allowed",
        )
    )
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


@app.post("/v1/runs/{run_id}/resume", response_model=RunRead, status_code=202)
def resume_run(
    run_id: str,
    body: ResumeInputCreate,
    idempotency_key: str = Header(min_length=8, max_length=200),
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    authorized_run(db, identity, run_id, WRITE_ROLES)
    run = resume_input_run(db, identity[0], run_id, identity[1], body.input_text, idempotency_key)
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
        db,
        identity[0],
        run_id,
        identity[1],
        body.decision,
        body.reason,
        reviewer_authorized=True,
    )
    db.commit()
    db.refresh(run)
    return run_read(run)


def _publication_approval_read(row: PublicationApproval) -> PublicationApprovalRead:
    return PublicationApprovalRead(
        id=row.id, run_id=row.run_id, action="draft_pr", destination=row.destination,
        base_commit=row.base_commit, patch_sha256=row.patch_sha256,
        test_evidence_sha256=row.test_evidence_sha256, status=row.status,
        expires_at=row.expires_at,
    )


@app.post(
    "/v1/runs/{run_id}/publication-approval",
    response_model=PublicationApprovalRead, status_code=201,
)
def approve_publication(
    run_id: str,
    body: PublicationApprovalCreate,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    authorized_run(db, identity, run_id, frozenset({"maintainer"}))
    row = approve_draft_pr(
        db, tenant_id=identity[0], run_id=run_id,
        connection_id=body.connection_id, base_branch=body.base_branch,
        actor=identity[1],
    )
    db.commit()
    db.refresh(row)
    return _publication_approval_read(row)


@app.get(
    "/v1/runs/{run_id}/publication-approval",
    response_model=PublicationApprovalRead,
)
def get_publication_approval(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    authorized_run(db, identity, run_id)
    row = db.scalar(select(PublicationApproval).where(
        PublicationApproval.tenant_id == identity[0],
        PublicationApproval.run_id == run_id,
    ))
    if row is None:
        raise ServiceError("NOT_FOUND", "Publication approval not found", 404)
    return _publication_approval_read(row)


@app.delete(
    "/v1/runs/{run_id}/publication-approval",
    response_model=PublicationApprovalRead,
)
def revoke_publication_approval(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    run = authorized_run(db, identity, run_id, frozenset({"maintainer"}))
    row = db.scalar(select(PublicationApproval).where(
        PublicationApproval.tenant_id == identity[0],
        PublicationApproval.run_id == run_id,
    ).with_for_update())
    if row is None:
        raise ServiceError("NOT_FOUND", "Publication approval not found", 404)
    if row.status == "approved":
        row.status = "revoked"
        db.add(AuditEvent(
            tenant_id=identity[0], actor=identity[1], action="publication.revoke_draft_pr",
            target_ref=row.id, arguments_hash=canonical_hash({"approval_id": row.id}),
            policy_revision=run.config_snapshot.get("policy_version", "1.0"),
            outcome="revoked",
        ))
        db.commit()
        db.refresh(row)
    return _publication_approval_read(row)


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
    review_event = db.scalar(
        select(RunEvent)
        .where(RunEvent.run_id == run.id, RunEvent.event_type == "review.decision")
        .order_by(RunEvent.sequence.desc())
        .limit(1)
    )
    approval = db.scalar(select(PublicationApproval).where(
        PublicationApproval.tenant_id == identity[0],
        PublicationApproval.run_id == run.id,
    ))
    publication_receipt = receipts.get("draft_pr_publication") or {}
    if publication_receipt.get("status") == "PUBLISHED":
        publication_status = "PUBLISHED"
    elif approval is None:
        publication_status = "DISABLED"
    elif approval.status == "approved":
        expiry = approval.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=utcnow().tzinfo)
        publication_status = "APPROVED" if expiry > utcnow() else "EXPIRED"
    else:
        publication_status = approval.status.upper()
    provider_mode = (candidate_patch or {}).get("provider_mode")
    media_urls = {}
    deleted_labels = {
        label for label in ("baseline", "candidate")
        if deletion_for(db, run, label) is not None
    }
    for label in ("baseline", "candidate"):
        if label in deleted_labels:
            continue
        media_receipt = receipts.get(f"media_{label}") or {}
        master = media_receipt.get("master")
        if media_receipt.get("status") == "READY" and isinstance(master, str):
            parts = Path(master).parts
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{64}", parts[-2]):
                media_urls[label] = f"/v1/runs/{run.id}/media/{label}/{parts[-2]}/master.m3u8"
    screenshot_urls = {
        label: f"/v1/runs/{run.id}/screenshot/{label}"
        for label, receipt in (("baseline", baseline_browser), ("candidate", candidate_browser))
        if receipt
        and receipt.get("final_screenshot") == "final.png"
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
        "diagnosis_evidence_refs": [
            f"/v1/runs/{run.id}/evidence/{step}/output"
            for step in ("named", "browser", "oracle")
            if isinstance((receipts.get(step) or {}).get("output_file"), str)
            and isinstance((receipts.get(step) or {}).get("output_sha256"), str)
        ],
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
        "publication_status": publication_status,
        "publication_url": (
            publication_receipt.get("pr_url") if publication_status == "PUBLISHED" else None
        ),
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
        "deleted_recording_labels": sorted(deleted_labels),
        "config_snapshot": run.config_snapshot,
    }


@app.get("/v1/runs/{run_id}/review-packet/download")
def download_review_packet(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    packet = review_packet(run_id, identity, db)
    return JSONResponse(
        packet,
        headers={
            "Content-Disposition": f'attachment; filename="aip-review-{packet["run_id"]}.json"',
        },
    )


@app.get("/v1/runs/{run_id}/evidence-bundle")
def download_evidence_bundle(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    packet = review_packet(run_id, identity, db)
    run = authorized_run(db, identity, run_id)
    actions = db.scalars(select(ToolAction).where(
        ToolAction.tenant_id == identity[0], ToolAction.run_id == run.id,
        ToolAction.status == "COMPLETED",
    )).all()
    receipts = {action.step_id: action.receipt or {} for action in actions}
    artifacts: dict[str, bytes] = {}

    def verified_bytes(response: FileResponse, expected: str) -> bytes:
        path = Path(response.path)
        try:
            if path.stat().st_size > 5_000_000:
                raise ServiceError("EVIDENCE_TOO_LARGE", "Evidence file exceeds archive policy", 409)
            content = path.read_bytes()
        except OSError as error:
            raise ServiceError("EVIDENCE_CHANGED", "Evidence changed during export", 409) from error
        if hashlib.sha256(content).hexdigest() != expected:
            raise ServiceError("EVIDENCE_CHANGED", "Evidence changed during export", 409)
        return content

    for label in packet["screenshot_urls"]:
        response = run_screenshot(run_id, label, identity, db)
        step = "browser" if label == "baseline" else "candidate_browser"
        artifacts[f"screenshots/{label}.png"] = verified_bytes(
            response, receipts[step]["screenshot_sha256"]
        )
    for step in EVIDENCE_DIRS:
        receipt = receipts.get(step) or {}
        if not isinstance(receipt.get("output_file"), str):
            continue
        response = run_evidence_output(run_id, step, identity, db)
        artifacts[f"logs/{step}.log"] = verified_bytes(response, receipt["output_sha256"])
    if packet["patch_url"]:
        artifacts["patch.diff"] = download_patch(run_id, identity, db).body
    try:
        archive = build_evidence_bundle(packet, artifacts)
    except BundleError as error:
        raise ServiceError("EVIDENCE_TOO_LARGE", str(error), 409) from error
    return Response(
        archive, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="aip-evidence-{run.id}.zip"'},
    )


@app.get("/v1/runs/{run_id}/patch")
def download_patch(
    run_id: str,
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    if settings().environment != "development":
        raise HTTPException(status_code=404)
    run = authorized_run(db, identity, run_id)
    action = db.scalar(
        select(ToolAction).where(
            ToolAction.run_id == run.id,
            ToolAction.tenant_id == identity[0],
            ToolAction.step_id == "candidate_patch",
            ToolAction.status == "COMPLETED",
        )
    )
    if action is None or action.receipt is None:
        raise ServiceError("PATCH_UNAVAILABLE", "No verified candidate patch exists", 404)
    diff = verified_fixture_diff(
        run,
        action.receipt,
        Path(settings().artifact_dir).resolve(),
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
            "qualified": qualification_current(m),
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
    try:
        model = register_model_entry(db, body, identity[1])
    except QualificationError as error:
        raise ServiceError(error.code, str(error), 409) from error
    db.commit()
    return {"id": model.id, "state": model.state}


@app.get("/v1/usage")
def usage(
    identity: tuple[str, str] = Depends(principal),
    db: Session = Depends(db_session),
):
    query = (
        select(BudgetEntry)
        .join(Run, BudgetEntry.run_id == Run.id)
        .where(BudgetEntry.tenant_id == identity[0], Run.tenant_id == identity[0])
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
    graph = None
    try:
        if settings().memgraph_uri:
            graph = MemgraphProjection(
                settings().memgraph_uri,
                settings().memgraph_user,
                settings().memgraph_password,
            )
        retrieval_mode, facts = connected_lookup(
            db, graph, identity[0], project_id, source_revision, query
        )
    except GraphUnavailable:
        retrieval_mode = "canonical_degraded"
        facts = scoped_lookup(db, identity[0], project_id, source_revision, query)
    finally:
        if graph is not None:
            graph.close()
    return {
        "retrieval_mode": retrieval_mode,
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
