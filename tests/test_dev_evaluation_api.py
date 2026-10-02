import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import platform_app.api as api_module
from platform_app.db import Base
from platform_app.models import Project, Run, ToolAction
from platform_app.service import ServiceError


def test_dev_evaluation_serves_only_current_local_media(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "evaluation"
    media_root = root / "private-media" / "fixture" / "candidate" / "media"
    effect_key = "a" * 64
    selected = media_root / effect_key
    selected.mkdir(parents=True)
    (selected / "master.m3u8").write_text("#EXTM3U\n")
    (media_root / "ready.json").write_text(json.dumps({
        "effect_key": effect_key, "master": f"{effect_key}/master.m3u8"
    }))
    (root / "candidate").mkdir()
    (root / "candidate" / "final.png").write_bytes(b"PNG")
    (root / "review-packet.json").write_text(json.dumps({
        "case_id": "test", "qualification_scope": "synthetic_host_fixture",
        "autonomous_repair": False, "candidate_origin": "manual", "verdict": "PASSED",
        "baseline_tree_sha256": "b", "candidate_tree_sha256": "c",
        "baseline": {"named_test": {"status": "PASSED"},
                     "browser": {"status": "FAILED"}, "oracle": {"status": "FAILED"}},
        "candidate": {"named_test": {"status": "PASSED"},
                      "browser": {"status": "PASSED", "final_screenshot": "final.png"},
                      "oracle": {"status": "PASSED"}},
        "local_media": {"baseline": {"status": "MISSING"},
                        "candidate": {"status": "READY"}},
    }))
    config = SimpleNamespace(
        environment="development", dev_token="", artifact_dir=str(root.parent),
        dev_evaluation_dir=str(root),
    )
    monkeypatch.setattr(api_module, "settings", lambda: config)
    client = TestClient(api_module.app)
    packet = client.get("/v1/dev/evaluation")
    assert packet.status_code == 200
    assert packet.json()["autonomous_repair"] is False
    url = packet.json()["results"]["candidate"]["media_manifest_url"]
    assert client.get(url).text.strip() == "#EXTM3U"
    invalid_url = "/v1/dev/evaluation/media/candidate/" + effect_key + "/unknown.txt"
    assert client.get(invalid_url).status_code == 404
    with pytest.raises(HTTPException):
        api_module.dev_evaluation_media("candidate", "../ready.json")
    config.dev_token = "configured"
    assert client.get(url).status_code == 404


def test_run_media_is_tenant_scoped_and_stays_within_published_effect(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    run_id = "11111111-1111-4111-8111-111111111111"
    tenant_id = "tenant-a"
    effect_key = "a" * 64
    media = root / "private-media" / tenant_id / f"{run_id}_baseline" / "media"
    selected = media / effect_key
    selected.mkdir(parents=True)
    (selected / "master.m3u8").write_text("#EXTM3U\n")
    (media / "ready.json").write_text(json.dumps({
        "effect_key": effect_key, "master": f"{effect_key}/master.m3u8"
    }))
    monkeypatch.setattr(api_module, "settings", lambda: SimpleNamespace(artifact_dir=str(root)))
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Project(id="project", tenant_id=tenant_id, name="Fixture"))
        db.add(Run(
            id=run_id, tenant_id=tenant_id, task_id="task", project_id="project",
            created_by="actor", idempotency_key="key", request_hash="r" * 64,
            base_commit="b" * 40, model_entry_id="model", config_snapshot={},
        ))
        db.commit()
        response = api_module.run_media(
            run_id, "baseline", f"{effect_key}/master.m3u8", (tenant_id, "actor"), db
        )
        assert response.path == selected / "master.m3u8"
        with pytest.raises(ServiceError) as wrong_tenant:
            api_module.run_media(
                run_id, "baseline", f"{effect_key}/master.m3u8", ("tenant-b", "actor"), db
            )
        assert wrong_tenant.value.status == 404
        with pytest.raises(HTTPException) as traversal:
            api_module.run_media(run_id, "baseline", "../ready.json", (tenant_id, "actor"), db)
        assert traversal.value.status_code == 404
        with pytest.raises(HTTPException) as wrong_effect:
            api_module.run_media(
                run_id, "baseline", f"{'b' * 64}/master.m3u8", (tenant_id, "actor"), db
            )
        assert wrong_effect.value.status_code == 404


def test_run_screenshot_requires_receipt_digest_and_tenant(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    run_id = "22222222-2222-4222-8222-222222222222"
    screenshot = root / run_id / "baseline" / "final.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    digest = hashlib.sha256(screenshot.read_bytes()).hexdigest()
    monkeypatch.setattr(api_module, "settings", lambda: SimpleNamespace(artifact_dir=str(root)))
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Project(id="project", tenant_id="tenant-a", name="Fixture"))
        db.add(Run(
            id=run_id, tenant_id="tenant-a", task_id="task", project_id="project",
            created_by="actor", idempotency_key="key", request_hash="r" * 64,
            base_commit="b" * 40, model_entry_id="model", config_snapshot={},
        ))
        db.add(ToolAction(
            tenant_id="tenant-a", run_id=run_id, step_id="browser",
            logical_action="fixture.browser", effect_key="e" * 64,
            arguments_hash="a" * 64, policy_result="allowed", status="COMPLETED",
            receipt={"final_screenshot": "final.png", "screenshot_sha256": digest},
        ))
        db.commit()
        response = api_module.run_screenshot(run_id, "baseline", ("tenant-a", "actor"), db)
        assert response.path == screenshot
        with pytest.raises(ServiceError) as foreign:
            api_module.run_screenshot(run_id, "baseline", ("tenant-b", "actor"), db)
        assert foreign.value.status == 404
        screenshot.write_bytes(b"tampered")
        with pytest.raises(HTTPException) as changed:
            api_module.run_screenshot(run_id, "baseline", ("tenant-a", "actor"), db)
        assert changed.value.status_code == 404
