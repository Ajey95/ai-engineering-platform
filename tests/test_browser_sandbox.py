from pathlib import Path

import pytest

from platform_app.browser_runner import safe_url, same_origin
from platform_app.dev_sandbox import SandboxError, docker_browser_command, prepare_workspace


def test_browser_navigation_stays_on_fixture_origin():
    origin = "http://127.0.0.1:8001"
    assert same_origin(safe_url(origin, "/tickets"), origin)
    with pytest.raises(ValueError):
        safe_url(origin, "//attacker.invalid/")
    with pytest.raises(ValueError):
        safe_url(origin, "/\\attacker.invalid/")
    assert not same_origin("http://127.0.0.1:8002/", origin)


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
