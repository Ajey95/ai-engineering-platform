"""Run an approved baseline plan as an unprivileged guest inside one VM."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from platform_app.browser_runner import run_scenario
from platform_app.environment_manifest import Command, EnvironmentManifest
from platform_app.sandbox_evidence import package_guest_evidence
from platform_app.verifier import run_named_test, tree_hash


def _log_receipt(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            count += len(chunk)
    return digest.hexdigest(), count


def _run_command(
    command: Command, workspace: Path, output: Path, name: str
) -> dict:
    started = time.monotonic()
    log = output / f"{name}.log"
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(workspace), "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    with log.open("wb") as stream:
        try:
            completed = subprocess.run(
                list(command.argv), cwd=workspace, env=safe_env,
                stdout=stream, stderr=subprocess.STDOUT,
                timeout=command.timeout_seconds, check=False,
            )
            exit_code = completed.returncode
            status = "PASSED" if exit_code == 0 else "FAILED"
        except subprocess.TimeoutExpired:
            exit_code = None
            status = "TIMEOUT"
    digest, size = _log_receipt(log)
    if size > 8_000_000:
        status = "OUTPUT_LIMIT"
    return {
        "status": status, "exit_code": exit_code,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "log_file": log.name, "log_sha256": digest, "log_bytes": size,
    }


def _runtime_matches(manifest: EnvironmentManifest) -> bool:
    if manifest.python_version is not None:
        actual = ".".join(str(part) for part in sys.version_info[:3])
        if not (
            actual == manifest.python_version
            or actual.startswith(manifest.python_version + ".")
        ):
            return False
    if manifest.node_version is not None:
        try:
            completed = subprocess.run(
                ["node", "--version"], capture_output=True, text=True,
                timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        actual = completed.stdout.strip().removeprefix("v")
        if completed.returncode or not (
            actual == manifest.node_version
            or actual.startswith(manifest.node_version + ".")
        ):
            return False
    return True


def run_environment(
    manifest: EnvironmentManifest, workspace: Path, artifacts: Path,
    *, case_id: str,
) -> dict:
    """Record baseline evidence; a repair verdict requires later independent work."""
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir() or artifacts.resolve().is_relative_to(workspace):
        raise ValueError("Guest artifacts must be outside the source workspace")
    artifacts.mkdir(parents=True, exist_ok=True)
    if manifest.external_destinations or manifest.environment_keys or manifest.postgres_fixture:
        raise ValueError(
            "This guest image lacks the requested network, secret or PostgreSQL fixture"
        )
    baseline_tree = tree_hash(workspace)
    result = {
        "schema_version": "1.0", "case_id": case_id,
        "manifest_sha256": manifest.digest(), "baseline_tree_sha256": baseline_tree,
        "status": "ENVIRONMENT_UNAVAILABLE", "preparation": [],
        "named_tests": {}, "browser": None,
    }
    if not _runtime_matches(manifest):
        result["environment_error"] = "RUNTIME_VERSION_MISMATCH"
        return result
    for stage, commands in (
        ("fixture", manifest.fixture_setup),
        ("install", manifest.install),
        ("build", manifest.build),
    ):
        for index, command in enumerate(commands):
            receipt = _run_command(command, workspace, artifacts, f"{stage}-{index}")
            result["preparation"].append({"stage": stage, **receipt})
            if receipt["status"] != "PASSED":
                return result
    runner_manifest = manifest.runner_manifest(case_id)
    for name in sorted(manifest.named_tests):
        result["named_tests"][name] = run_named_test(
            runner_manifest, name, workspace, artifacts
        )
    browser_dir = artifacts / "browser"
    result["browser"] = asyncio.run(run_scenario(
        runner_manifest, workspace, browser_dir
    ))
    result["status"] = "BASELINE_RECORDED"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() == 0:
        parser.error("Guest runner must execute as an unprivileged Linux user")
    import resource

    resource.setrlimit(resource.RLIMIT_FSIZE, (20_000_000, 20_000_000))
    resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    manifest = EnvironmentManifest.model_validate_json(args.manifest.read_bytes())
    result = run_environment(
        manifest, args.workspace, args.artifacts, case_id=args.case_id
    )
    (args.artifacts / "result.json").write_text(
        json.dumps(result, sort_keys=True), encoding="utf-8"
    )
    package_guest_evidence(args.artifacts)
    return 0 if result["status"] == "BASELINE_RECORDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
