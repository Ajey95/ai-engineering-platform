from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_app import api, auth
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import (
    Project,
    ProjectMembership,
    PublicationApproval,
    RepositoryConnection,
    Run,
    RunEvent,
    Task,
    Tenant,
    TenantMembership,
    ToolAction,
)
from platform_app.publication import approve_draft_pr, verify_draft_pr_approval
from platform_app.service import ServiceError


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'publish.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _seed(db: Session, *, provider_mode: str = "native_api") -> Run:
    tenant = Tenant(id="tenant", name="Tenant")
    project = Project(id="project", tenant_id=tenant.id, name="Project")
    task = Task(
        id="task", tenant_id=tenant.id, project_id=project.id,
        report="A test form fails", expected_behavior="submit", actual_behavior="error",
        created_by="author",
    )
    connection = RepositoryConnection(
        id="connection", tenant_id=tenant.id, project_id=project.id,
        provider="github", repository_ref="team/repo", status="ready", created_by="owner",
    )
    run = Run(
        id="run", tenant_id=tenant.id, project_id=project.id, task_id=task.id,
        created_by="author", idempotency_key="key", request_hash="a" * 64,
        base_commit="b" * 40, model_entry_id="model", state="COMPLETED",
        verdict="PASSED", config_snapshot={
            "policy_version": "1.0", "repository_connection_id": connection.id,
        },
    )
    db.add_all([tenant, project, task, connection, run])
    db.flush()
    db.add(RunEvent(
        tenant_id=tenant.id, run_id=run.id, sequence=1,
        event_type="review.decision", payload={"decision": "accepted"},
    ))
    for step in ("candidate_patch", "candidate_named", "candidate_browser", "candidate_oracle"):
        receipt = (
            {
                "status": "COMPLETED", "provider_mode": provider_mode,
                "patch_sha256": "c" * 64, "changed_files": ["server.py"],
            }
            if step == "candidate_patch" else {"status": "PASSED", "tree_sha256": "d" * 64}
        )
        db.add(ToolAction(
            tenant_id=tenant.id, run_id=run.id, step_id=step,
            logical_action="fixture.patch" if step == "candidate_patch" else "fixture.check",
            effect_key=step, arguments_hash="e" * 64, policy_result="allowed",
            status="COMPLETED", receipt=receipt,
        ))
    db.commit()
    return run


def test_draft_pr_approval_is_bound_and_expires(tmp_path):
    engine = _db(tmp_path)
    now = datetime.now(UTC)
    with Session(engine) as db:
        run = _seed(db)
        row = approve_draft_pr(
            db, tenant_id=run.tenant_id, run_id=run.id,
            connection_id="connection", base_branch="main", actor="maintainer", now=now,
        )
        db.commit()
        assert row.destination == "github:team/repo@main"
        assert row.expires_at.replace(tzinfo=UTC) - now == timedelta(hours=24)
        assert approve_draft_pr(
            db, tenant_id=run.tenant_id, run_id=run.id,
            connection_id="connection", base_branch="main", actor="maintainer", now=now,
        ).id == row.id
        verify_draft_pr_approval(db, row, now=now)
        with pytest.raises(ServiceError, match="Existing approval differs"):
            approve_draft_pr(
                db, tenant_id=run.tenant_id, run_id=run.id,
                connection_id="connection", base_branch="release", actor="maintainer", now=now,
            )
        with pytest.raises(ServiceError, match="expired"):
            verify_draft_pr_approval(db, row, now=now + timedelta(hours=25))
        action = db.query(ToolAction).filter(ToolAction.step_id == "candidate_browser").one()
        action.receipt = {"status": "FAILED"}
        db.flush()
        with pytest.raises(ServiceError, match="did not pass"):
            verify_draft_pr_approval(db, row, now=now)
        assert db.query(PublicationApproval).count() == 1
    engine.dispose()


def test_controlled_provider_and_unready_repository_cannot_be_approved(tmp_path):
    engine = _db(tmp_path)
    with Session(engine) as db:
        run = _seed(db, provider_mode="controlled_test")
        with pytest.raises(ServiceError, match="Verified live patch"):
            approve_draft_pr(
                db, tenant_id=run.tenant_id, run_id=run.id,
                connection_id="connection", base_branch="main", actor="maintainer",
            )
        connection = db.get(RepositoryConnection, "connection")
        connection.status = "unverified"
        db.flush()
        with pytest.raises(ServiceError, match="destination is not ready"):
            approve_draft_pr(
                db, tenant_id=run.tenant_id, run_id=run.id,
                connection_id="connection", base_branch="main", actor="maintainer",
            )
    engine.dispose()


def test_publication_approval_api_requires_maintainer_and_revokes(tmp_path, monkeypatch):
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
    )
    monkeypatch.setattr(api, "settings", lambda: config)
    monkeypatch.setattr(auth, "settings", lambda: config)
    engine = _db(tmp_path)
    with Session(engine) as db:
        _seed(db)
        db.add_all([
            TenantMembership(tenant_id="tenant", subject="alice", role="member"),
            TenantMembership(tenant_id="tenant", subject="bob", role="member"),
            ProjectMembership(
                tenant_id="tenant", project_id="project", subject="alice", role="maintainer",
            ),
            ProjectMembership(
                tenant_id="tenant", project_id="project", subject="bob", role="viewer",
            ),
        ])
        db.commit()

    def session():
        with Session(engine) as db:
            yield db

    actor = ["bob"]
    api.app.dependency_overrides[api.db_session] = session
    api.app.dependency_overrides[api.principal] = lambda: ("tenant", actor[0])
    try:
        client = TestClient(api.app)
        path = "/v1/runs/run/publication-approval"
        body = {"connection_id": "connection", "base_branch": "main"}
        assert client.post(path, json=body).status_code == 404
        actor[0] = "alice"
        created = client.post(path, json=body)
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "approved"
        assert client.get(path).json()["id"] == created.json()["id"]
        assert client.delete(path).json()["status"] == "revoked"
    finally:
        api.app.dependency_overrides.clear()
        engine.dispose()
