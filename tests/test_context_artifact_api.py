import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api, auth
from platform_app.config import Settings
from platform_app.context_compaction import compact_general_context
from platform_app.db import Base
from platform_app.models import (
    Project,
    ProjectMembership,
    Run,
    RunEvent,
    Task,
    Tenant,
    TenantMembership,
)


def test_recorded_context_retrieval_requires_run_access_and_valid_lineage(
    tmp_path, monkeypatch,
):
    prompt = {
        "schema_version": "1.0", "task": {"report": "Fix submit"},
        "base_commit": "a" * 40, "source_archive_sha256": "b" * 64,
        "allowed_paths": ["app.py"],
        "source_items": [{"path": "app.py", "base_sha256": "c" * 64,
                          "content": "def submit():\n    return False\n"}],
        "indexed_code": [], "selected_memory": [], "prior_attempts": [],
        "baseline": {"manifest_sha256": "d" * 64, "named_tests": {"unit": "FAILED"},
                     "browser_status": "FAILED", "browser_steps": [],
                     "page_errors": [], "log_excerpts": [{
                         "path": "test-unit.log", "tail": "x" * 3000 + "FAIL",
                         "truncated": False,
                     }]},
    }
    _, summary_ref = compact_general_context(prompt, 4000, tmp_path, "run-a")
    assert summary_ref is not None
    digest = summary_ref.removesuffix(".json").split("summary-")[-1]
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys", artifact_dir=str(tmp_path),
    )
    monkeypatch.setattr(api, "settings", lambda: config)
    monkeypatch.setattr(auth, "settings", lambda: config)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B"),
            Project(id="project-a", tenant_id="tenant-a", name="Project"),
            Task(id="task-a", tenant_id="tenant-a", project_id="project-a",
                 report="Fix submit", expected_behavior="success",
                 actual_behavior="failure", created_by="alice"),
            Run(id="run-a", tenant_id="tenant-a", project_id="project-a",
                task_id="task-a", created_by="alice", idempotency_key="key-a",
                request_hash="e" * 64, base_commit="a" * 40,
                model_entry_id="model-a", state="INVESTIGATING",
                config_snapshot={"execution_profile": "hosted_vm_v1"}),
            TenantMembership(tenant_id="tenant-a", subject="alice", role="member"),
            TenantMembership(tenant_id="tenant-a", subject="bob", role="member"),
            TenantMembership(tenant_id="tenant-b", subject="eve", role="member"),
            ProjectMembership(tenant_id="tenant-a", project_id="project-a",
                              subject="alice", role="viewer"),
            RunEvent(tenant_id="tenant-a", run_id="run-a", sequence=1,
                     event_type="context.compacted", payload={"summary_ref": summary_ref}),
        ])
        db.commit()

    def session():
        with Session(engine) as db:
            yield db

    actor = [("tenant-a", "alice")]
    api.app.dependency_overrides[api.db_session] = session
    api.app.dependency_overrides[api.principal] = lambda: actor[0]
    try:
        client = TestClient(api.app)
        path = f"/v1/runs/run-a/context/{digest}"
        summary = client.get(path)
        assert summary.status_code == 200, summary.text
        assert summary.headers["cache-control"] == "no-store"
        assert summary.json()["source"] is None
        original = client.get(path, params={"include_source": "true"})
        assert original.status_code == 200, original.text
        assert original.json()["source"] == prompt
        assert client.get(path.replace(digest, "0" * 64)).status_code == 404
        actor[0] = ("tenant-a", "bob")
        assert client.get(path, params={"include_source": "true"}).status_code == 404
        actor[0] = ("tenant-b", "eve")
        assert client.get(path).status_code == 404
        actor[0] = ("tenant-a", "alice")
        source_ref = original.json()["summary"]["source_bundle_ref"]
        (tmp_path / source_ref).write_text(json.dumps({"changed": True}))
        assert client.get(path, params={"include_source": "true"}).status_code == 409
    finally:
        api.app.dependency_overrides.clear()
        engine.dispose()
