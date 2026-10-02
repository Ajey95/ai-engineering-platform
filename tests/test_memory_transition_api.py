from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_app import api, auth
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.memory import propose_fact, verify_fact
from platform_app.models import Project, ProjectMembership, Tenant, TenantMembership


def test_memory_transition_roles_and_scope(tmp_path, monkeypatch):
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
    )
    monkeypatch.setattr(api, "settings", lambda: config)
    monkeypatch.setattr(auth, "settings", lambda: config)
    engine = create_engine(f"sqlite:///{(tmp_path / 'memory-api.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B"),
            Project(id="project-a", tenant_id="tenant-a", name="A"),
            Project(id="project-b", tenant_id="tenant-b", name="B"),
            TenantMembership(tenant_id="tenant-a", subject="alice", role="member"),
            TenantMembership(tenant_id="tenant-a", subject="bob", role="member"),
            ProjectMembership(
                tenant_id="tenant-a", project_id="project-a", subject="alice",
                role="maintainer",
            ),
            ProjectMembership(
                tenant_id="tenant-a", project_id="project-a", subject="bob", role="reviewer",
            ),
        ])
        db.flush()
        proposed = propose_fact(
            db, "tenant-a", "project-a", "repo", "a" * 40,
            "project_fact", "upload.py", "Unsupported claim", ["test:123"],
        )
        verified = propose_fact(
            db, "tenant-a", "project-a", "repo", "a" * 40,
            "project_fact", "upload.py", "Verified claim", ["test:456"],
        )
        verify_fact(db, verified, "test:456", "unit test only", "test tool")
        db.commit()
        proposed_id, verified_id = proposed.id, verified.id

    def session():
        with Session(engine) as db:
            yield db

    actor = ["bob"]
    api.app.dependency_overrides[api.db_session] = session
    api.app.dependency_overrides[api.principal] = lambda: ("tenant-a", actor[0])
    try:
        client = TestClient(api.app)
        root = "/v1/projects/project-a/memory"
        initial = client.get(f"{root}/records")
        assert initial.status_code == 200, initial.text
        assert {fact["status"] for fact in initial.json()["facts"]} == {
            "proposed", "verified",
        }
        assert len(initial.json()["facts"][0]["history"]) >= 1
        rejected = client.post(f"{root}/{proposed_id}/transition", json={
            "action": "reject", "reason": "Independent evidence is missing",
        })
        assert rejected.status_code == 202, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert client.post(f"{root}/{verified_id}/transition", json={
            "action": "expire", "reason": "Configuration changed upstream",
        }).status_code == 404
        actor[0] = "alice"
        expired = client.post(f"{root}/{verified_id}/transition", json={
            "action": "expire", "reason": "Configuration changed upstream",
        })
        assert expired.status_code == 202, expired.text
        assert expired.json()["status"] == "expired"
        records = client.get(f"{root}/records?status=expired&source_revision={'a' * 40}")
        assert records.status_code == 200, records.text
        assert [fact["id"] for fact in records.json()["facts"]] == [verified_id]
        assert [event["status"] for event in records.json()["facts"][0]["history"]] == [
            "proposed", "verified", "expired",
        ]
        assert client.get(f"{root}/records?limit=101").status_code == 400
        assert client.get("/v1/projects/project-b/memory/records").status_code == 404
        assert client.post(f"/v1/projects/project-b/memory/{verified_id}/transition", json={
            "action": "reject", "reason": "Wrong tenant record",
        }).status_code == 404
    finally:
        api.app.dependency_overrides.clear()
        engine.dispose()
