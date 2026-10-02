import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_app import api
from platform_app.db import Base
from platform_app.models import Project, Run, Task, Tenant, ToolAction


def test_evidence_download_is_scoped_and_hash_verified(tmp_path, monkeypatch):
    run_id = "11111111-1111-4111-8111-111111111111"
    root = tmp_path / "artifacts"
    baseline = root / run_id / "baseline"
    baseline.mkdir(parents=True)
    original = b"FAILED assertion with full diagnostic log\n"
    log = baseline / "named.log"
    log.write_bytes(original)
    engine = create_engine(f"sqlite:///{(tmp_path / 'evidence.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="local-tenant", name="Local"))
        db.add(Project(id="project", tenant_id="local-tenant", name="Project"))
        db.add(Task(
            id="task", tenant_id="local-tenant", project_id="project",
            report="Failure", expected_behavior="pass", actual_behavior="fail",
            created_by="actor",
        ))
        db.add(Run(
            id=run_id, tenant_id="local-tenant", project_id="project", task_id="task",
            created_by="actor", idempotency_key="key", request_hash="r" * 64,
            base_commit="a" * 40, model_entry_id="model", state="INCONCLUSIVE",
            config_snapshot={"policy_version": "1.0"},
        ))
        db.add(ToolAction(
            tenant_id="local-tenant", run_id=run_id, step_id="named",
            logical_action="fixture.named", effect_key="e" * 64,
            arguments_hash="a" * 64, policy_result="allowed", status="COMPLETED",
            receipt={
                "status": "FAILED", "output_file": "named.log",
                "output_sha256": hashlib.sha256(original).hexdigest(),
            },
        ))
        db.commit()

        def session_override():
            yield db

        api.app.dependency_overrides[api.db_session] = session_override
        api.app.dependency_overrides[api.principal] = lambda: ("local-tenant", "actor")
        monkeypatch.setattr(api, "settings", lambda: SimpleNamespace(
            artifact_dir=str(root), environment="development", dev_token="test-token",
        ))
        try:
            client = TestClient(api.app)
            url = f"/v1/runs/{run_id}/evidence/named/output"
            response = client.get(url)
            assert response.status_code == 200
            assert response.content == original
            assert response.headers["content-type"] == "application/octet-stream"
            packet = client.get(f"/v1/runs/{run_id}/review-packet")
            assert packet.status_code == 200
            assert packet.json()["diagnosis_evidence_refs"] == [url]
            download = client.get(f"/v1/runs/{run_id}/review-packet/download")
            assert download.status_code == 200
            assert download.json() == packet.json()
            assert download.headers["content-disposition"].startswith("attachment;")
            assert download.headers["cache-control"] == "no-store"
            assert client.get(url.replace("named", "unknown")).status_code == 404
            api.app.dependency_overrides[api.principal] = lambda: ("other-tenant", "actor")
            assert client.get(url).status_code == 404
            assert client.get(f"/v1/runs/{run_id}/review-packet/download").status_code == 404
            api.app.dependency_overrides[api.principal] = lambda: ("local-tenant", "actor")
            log.write_bytes(b"tampered")
            assert client.get(url).status_code == 404
        finally:
            api.app.dependency_overrides.clear()
    engine.dispose()
