import asyncio
import hashlib
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from platform_app import dev_sandbox
from platform_app.browser_runner import run_scenario, safe_url, same_origin
from platform_app.dev_sandbox import (
    SandboxError,
    docker_browser_command,
    docker_verifier_command,
    prepare_workspace,
    run_browser_fixture,
)
from platform_app.development_worker import _verified_receipt
from platform_app.service import ServiceError


def test_browser_navigation_stays_on_fixture_origin():
    origin = "http://127.0.0.1:8001"
    assert same_origin(safe_url(origin, "/tickets"), origin)
    with pytest.raises(ValueError):
        safe_url(origin, "//attacker.invalid/")
    with pytest.raises(ValueError):
        safe_url(origin, "/\\attacker.invalid/")
    assert not same_origin("http://127.0.0.1:8002/", origin)
    assert not same_origin("http://127.0.0.1:invalid/", origin)


def test_browser_rejects_external_subresource(tmp_path: Path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    (tmp_path / "index.html").write_text(
        '<h1>Ready</h1><img src="https://example.invalid/track.png">',
        encoding="utf-8",
    )
    origin = f"http://127.0.0.1:{port}"
    manifest = {
        "allowed_origin": origin,
        "health_url": origin + "/",
        "case_id": "blocked-subresource",
        "start_command": [
            sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
        ],
        "require_instance_header": False,
        "scenario": {"steps": [{"action": "goto", "path": "/"}]},
    }
    result = asyncio.run(run_scenario(manifest, tmp_path, tmp_path / "artifacts"))
    assert result["status"] == "FAILED"
    assert result["blocked_requests"] >= 1
    assert result["security_error"]
    video = tmp_path / "artifacts" / result["recording"]
    assert result["recording_sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert _verified_receipt("browser", tmp_path / "artifacts", result) == result
    video.write_bytes(b"changed")
    with pytest.raises(ServiceError) as changed:
        _verified_receipt("browser", tmp_path / "artifacts", result)
    assert changed.value.code == "EFFECT_OUTCOME_UNKNOWN"


def test_workspace_copy_rejects_symlink(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ordinary.py").write_text("pass")
    prepare_workspace(source, tmp_path / "good")
    assert (tmp_path / "good" / "ordinary.py").read_text() == "pass"
    try:
        (source / "escape").symlink_to(tmp_path / "good", target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create a symlink")
    with pytest.raises(SandboxError):
        prepare_workspace(source, tmp_path / "bad")


def test_dev_container_has_no_network_or_host_privileges(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    command = docker_browser_command(
        "aip-dev-sandbox:0.1.0", workspace, manifest, artifacts, "aip-dev-test"
    )
    for pair in (("--network", "none"), ("--cap-drop", "ALL")):
        index = command.index(pair[0])
        assert command[index + 1] == pair[1]
    assert "--read-only" in command
    assert "--privileged" not in command
    assert "/var/run/docker.sock" not in " ".join(command)
    user_index = command.index("--user")
    assert command[user_index + 1] != "0:0"
    if os.name == "nt":
        wsl_command = docker_browser_command(
            "aip-dev-sandbox:0.1.0", workspace, manifest, artifacts,
            "aip-dev-test", runtime="wsl",
        )
        assert wsl_command[:7] == [
            "wsl", "-d", "Ubuntu-24.04", "-u", "root", "--", "docker"
        ]
        assert "/mnt/" in " ".join(wsl_command)


def test_hidden_oracle_is_only_mounted_for_oracle_verification(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    oracle = tmp_path / "hidden.py"
    oracle.write_text("assert True")
    named = docker_verifier_command(
        "aip-dev-sandbox:0.1.0", workspace, manifest, artifacts,
        "aip-dev-named", "named",
    )
    hidden = docker_verifier_command(
        "aip-dev-sandbox:0.1.0", workspace, manifest, artifacts,
        "aip-dev-oracle", "oracle", oracle=oracle,
    )
    assert "/opt/oracle.py" not in " ".join(named)
    assert "/opt/oracle.py" in " ".join(hidden)
    with pytest.raises(ValueError):
        docker_verifier_command(
            "aip-dev-sandbox:0.1.0", workspace, manifest, artifacts,
            "aip-dev-invalid", "named", oracle=oracle,
        )


def test_failed_container_cannot_reuse_previous_evidence(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "result.json").write_text('{"status":"FAILED"}')
    monkeypatch.setattr(
        dev_sandbox.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "startup failed"),
    )
    with pytest.raises(SandboxError, match="did not produce evidence"):
        run_browser_fixture(
            "aip-dev-sandbox:0.1.0", workspace, manifest, artifacts,
            "aip-dev-stale-test",
        )
