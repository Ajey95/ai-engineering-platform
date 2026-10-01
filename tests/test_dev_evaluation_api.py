import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import platform_app.api as api_module


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
