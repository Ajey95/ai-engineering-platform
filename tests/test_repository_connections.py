from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api, auth
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import (
    AuditEvent,
    Project,
    ProjectMembership,
    RepositoryConnection,
    Tenant,
    TenantMembership,
)
from platform_app.repository_connections import github_repository_ref
from platform_app.service import ServiceError


def test_github_ref_rejects_credential_and_non_github_origins():
    assert github_repository_ref("https://github.com/Org/Repo.git") == "org/repo"
    for url in (
        "https://token@github.com/org/repo", "https://github.com.evil.test/org/repo",
        "http://github.com/org/repo", "https://github.com/org/repo?token=secret",
        "https://github.com/org/repo/extra", "https://github.com/org/%2e%2e",
    ):
        try:
            github_repository_ref(url)
        except ServiceError as error:
            assert error.status == 400
        else:
            raise AssertionError(f"Accepted unsafe GitHub URL: {url}")


def test_scoped_repository_connection_api_and_secret_reference(monkeypatch):
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
    )
    monkeypatch.setattr(api, "settings", lambda: config)
    monkeypatch.setattr(auth, "settings", lambda: config)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B"),
            TenantMembership(tenant_id="tenant-a", subject="alice", role="member"),
            TenantMembership(tenant_id="tenant-a", subject="bob", role="member"),
            Project(id="project-a", tenant_id="tenant-a", name="A"),
            Project(id="project-b", tenant_id="tenant-b", name="B"),
            ProjectMembership(
                tenant_id="tenant-a", project_id="project-a", subject="alice",
                role="maintainer",
            ),
            ProjectMembership(
                tenant_id="tenant-a", project_id="project-a", subject="bob", role="viewer",
            ),
        ])
        db.commit()

    def session():
        with Session(engine) as db:
            yield db

    actor = ["alice"]
    api.app.dependency_overrides[api.db_session] = session
    api.app.dependency_overrides[api.principal] = lambda: ("tenant-a", actor[0])
    try:
        client = TestClient(api.app)
        path = "/v1/projects/project-a/repository-connections"
        body = {
            "repository_url": "https://github.com/Org/Repo.git",
            "credential_ref": "secret://github/app-installation-1",
        }
        response = client.post(path, json=body)
        assert response.status_code == 201, response.text
        connection_id = response.json()["id"]
        assert response.json()["repository_ref"] == "org/repo"
        assert response.json()["readiness"] == "verification_required"
        assert "credential_ref" not in response.json()
        assert client.post(path, json=body).json()["id"] == connection_id
        assert client.get(path).json()[0]["id"] == connection_id
        bad = client.post(path, json={**body, "credential_ref": "ghp_raw_token"})
        assert bad.status_code == 400
        assert client.get(path.replace("project-a", "project-b")).status_code == 404
        actor[0] = "bob"
        assert client.get(path).status_code == 200
        assert client.post(path, json=body).status_code == 404
        assert client.delete(f"{path}/{connection_id}").status_code == 404
        actor[0] = "alice"
        assert client.delete(f"{path}/{connection_id}").json()["readiness"] == "disabled"
        with Session(engine) as db:
            row = db.scalar(select(RepositoryConnection).where(
                RepositoryConnection.id == connection_id
            ))
            assert row.status == "disabled"
            assert db.query(AuditEvent).filter(
                AuditEvent.action == "repository.connection_set"
            ).count() == 1
    finally:
        api.app.dependency_overrides.clear()
        engine.dispose()
