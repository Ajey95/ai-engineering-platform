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


def _docker_prefix(runtime: str, wsl_distro: str) -> list[str]:
    if runtime == "native":
        return ["docker"]
    if runtime == "wsl" and wsl_distro and all(
        character.isalnum() or character in "-_." for character in wsl_distro
    ):
        return ["wsl", "-d", wsl_distro, "-u", "root", "--", "docker"]
    raise ValueError("Unsupported Docker runtime")


def _mount_source(path: Path, runtime: str) -> str:
    resolved = path.resolve(strict=True)
    if runtime == "native":
        return str(resolved)
    if os.name != "nt":
        return str(resolved)
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise ValueError("WSL mount source must be on a local drive")
    return f"/mnt/{drive}/{resolved.relative_to(resolved.anchor).as_posix()}"


def _container_identity(runtime: str) -> tuple[int, int]:
    # Native Linux bind mounts retain host ownership. Match the unprivileged
    # runner so the sandbox can read its pinned workspace and write evidence.
    if runtime == "native" and hasattr(os, "getuid") and os.getuid() != 0:
        return os.getuid(), os.getgid()
    return 10001, 10001


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


def _container_base_command(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str,
    runtime: str, wsl_distro: str, oracle: Path | None = None,
) -> list[str]:
    if not name.startswith("aip-dev-") or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("Invalid development sandbox name")
    for path in (workspace, manifest, artifacts, oracle):
        if path is None:
            continue
        if not path.resolve(strict=True).exists():
            raise ValueError("Sandbox inputs must exist")
    uid, gid = _container_identity(runtime)
    command = [
        *_docker_prefix(runtime, wsl_distro), "run", "--rm", "--init", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--cpus", "2", "--memory", "4g", "--shm-size", "1g",
        "--user", f"{uid}:{gid}", "--env", "HOME=/home/pwuser",
        "--tmpfs", "/tmp:rw,nosuid,size=1073741824",
        "--tmpfs", f"/home/pwuser:rw,nosuid,size=67108864,uid={uid},gid={gid}",
        "--mount", f"type=bind,src={_mount_source(workspace, runtime)},dst=/workspace,readonly",
        "--mount", (
            f"type=bind,src={_mount_source(manifest, runtime)},"
            "dst=/opt/platform/manifest.json,readonly"
        ),
        "--mount", f"type=bind,src={_mount_source(artifacts, runtime)},dst=/artifacts",
    ]
    if oracle is not None:
        command.extend([
            "--mount", f"type=bind,src={_mount_source(oracle, runtime)},dst=/opt/oracle.py,readonly"
        ])
    return [*command, image]


def docker_browser_command(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str,
    runtime: str = "native", wsl_distro: str = "Ubuntu-24.04",
) -> list[str]:
    return [
        *_container_base_command(image, workspace, manifest, artifacts, name, runtime, wsl_distro),
        "python", "/opt/platform/browser_runner.py",
        "--manifest", "/opt/platform/manifest.json",
        "--workspace", "/workspace", "--artifacts", "/artifacts",
    ]


def docker_verifier_command(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str,
    mode: str, test_name: str = "baseline", oracle: Path | None = None,
    runtime: str = "native", wsl_distro: str = "Ubuntu-24.04",
) -> list[str]:
    if mode not in {"named", "oracle"} or (mode == "oracle") != (oracle is not None):
        raise ValueError("Oracle mount must match verifier mode")
    command = [
        *_container_base_command(
            image, workspace, manifest, artifacts, name, runtime, wsl_distro, oracle
        ),
        "python", "/opt/platform/container_verifier.py",
        "--mode", mode, "--manifest", "/opt/platform/manifest.json",
        "--workspace", "/workspace", "--artifacts", "/artifacts",
    ]
    if mode == "named":
        command.extend(["--name", test_name])
    else:
        command.extend(["--oracle", "/opt/oracle.py"])
    return command


def _run_container(
    command: list[str], artifacts: Path, result_name: str, name: str,
    runtime: str, wsl_distro: str, timeout_seconds: int,
) -> dict:
    result_path = artifacts / result_name
    # A retry must never accept a receipt left by a previous container.
    result_path.unlink(missing_ok=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        # Docker --rm does not remove a container until its process exits.
        subprocess.run(
            [*_docker_prefix(runtime, wsl_distro), "kill", name],
            capture_output=True, timeout=15,
        )
        raise SandboxError("Fixture exceeded its runtime limit") from error
    if not result_path.is_file():
        raise SandboxError(
            f"Fixture did not produce evidence (exit {completed.returncode}): "
            f"{completed.stderr[-500:]}"
        )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (result["status"] == "PASSED") != (completed.returncode == 0):
        raise SandboxError("Runner exit status conflicts with its result artifact")
    return result


def run_browser_fixture(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str,
    timeout_seconds: int = 180,
    runtime: str = "native", wsl_distro: str = "Ubuntu-24.04",
) -> dict:
    artifacts.mkdir(parents=True, exist_ok=True)
    command = docker_browser_command(
        image, workspace, manifest, artifacts, name, runtime, wsl_distro
    )
    return _run_container(
        command, artifacts, "result.json", name, runtime, wsl_distro, timeout_seconds
    )


def run_verifier_fixture(
    image: str, workspace: Path, manifest: Path, artifacts: Path, name: str,
    mode: str, test_name: str = "baseline", oracle: Path | None = None,
    timeout_seconds: int = 180,
    runtime: str = "native", wsl_distro: str = "Ubuntu-24.04",
) -> dict:
    artifacts.mkdir(parents=True, exist_ok=True)
    command = docker_verifier_command(
        image, workspace, manifest, artifacts, name, mode, test_name, oracle, runtime, wsl_distro
    )
    result_name = f"test-{test_name}.json" if mode == "named" else "oracle.json"
    return _run_container(
        command, artifacts, result_name, name, runtime, wsl_distro, timeout_seconds
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="aip-dev-sandbox:0.1.0")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--runtime", choices=["native", "wsl"], default="native")
    parser.add_argument("--wsl-distro", default="Ubuntu-24.04")
    args = parser.parse_args()
    result = run_browser_fixture(
        args.image, args.workspace, args.manifest, args.artifacts, args.name,
        runtime=args.runtime, wsl_distro=args.wsl_distro,
    )
    print(json.dumps({"case_id": result["case_id"], "status": result["status"]}))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
