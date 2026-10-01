"""Development Docker adapter for synthetic fixture execution (FR-SBX-01).

Containers here are useful for controlled fixtures; they are not the P1
isolation boundary for hostile customer repositories.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


class SandboxError(Exception):
    pass


SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "node_modules", ".venv"}


def prepare_workspace(source: Path, destination: Path) -> None:
    """Copy only ordinary files and directories; reject links and reparse points."""
    source = source.resolve(strict=True)
    if destination.exists():
        raise SandboxError("Destination already exists")
    destination.mkdir(parents=True)
    for root, dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative = root_path.relative_to(source)
        target_dir = destination / relative
        for name in dirs + files:
            selected = root_path / name
            if selected.is_symlink() or os.path.isjunction(selected):
                raise SandboxError(f"Linked path is not allowed: {relative / name}")
            if not selected.resolve(strict=True).is_relative_to(source):
                raise SandboxError("Source path escaped workspace")
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in dirs:
            (target_dir / name).mkdir()
        for name in files:
            selected = root_path / name
            if not selected.is_file():
                raise SandboxError("Only regular fixture files are accepted")
            shutil.copy2(selected, target_dir / name)


def docker_browser_command(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str
) -> list[str]:
    if not name.startswith("aip-dev-") or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("Invalid development sandbox name")
    for path in (workspace, manifest, artifacts):
        if not path.resolve(strict=True).exists():
            raise ValueError("Sandbox inputs must exist")
    return [
        "docker", "run", "--rm", "--init", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--cpus", "2", "--memory", "4g", "--shm-size", "1g",
        "--tmpfs", "/tmp:rw,nosuid,size=1073741824",
        "--mount", f"type=bind,src={workspace.resolve()},dst=/workspace",
        "--mount", f"type=bind,src={manifest.resolve()},dst=/opt/platform/manifest.json,readonly",
        "--mount", f"type=bind,src={artifacts.resolve()},dst=/artifacts",
        image, "python", "/opt/platform/browser_runner.py",
        "--manifest", "/opt/platform/manifest.json",
        "--workspace", "/workspace", "--artifacts", "/artifacts",
    ]


def run_browser_fixture(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str,
    timeout_seconds: int = 180,
) -> dict:
    artifacts.mkdir(parents=True, exist_ok=True)
    command = docker_browser_command(image, workspace, manifest, artifacts, name)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        # Docker --rm does not remove a container until its process exits.
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=15)
        raise SandboxError("Fixture exceeded its runtime limit") from error
    result_path = artifacts / "result.json"
    if not result_path.is_file():
        raise SandboxError(
            f"Fixture did not produce browser evidence (exit {completed.returncode}): "
            f"{completed.stderr[-500:]}"
        )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result["status"] == "PASSED" and completed.returncode != 0:
        raise SandboxError("Runner exit status conflicts with its result artifact")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="aip-dev-sandbox:0.1.0")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    result = run_browser_fixture(
        args.image, args.workspace, args.manifest, args.artifacts, args.name
    )
    print(json.dumps({"case_id": result["case_id"], "status": result["status"]}))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
