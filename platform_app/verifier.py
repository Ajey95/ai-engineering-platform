"""Deterministic fixture test receipts for FR-REP-02.

The command is selected by a trusted environment manifest. This module is run
inside a sandbox for target repositories; host use is limited to synthetic
fixtures during development.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "node_modules", ".venv"}


def tree_hash(workspace: Path) -> str:
    workspace = workspace.resolve(strict=True)
    digest = hashlib.sha256()
    files: list[Path] = []
    for root, dirs, names in os.walk(workspace, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in names:
            path = Path(root) / name
            if path.is_symlink() or os.path.isjunction(path):
                raise ValueError("Workspace contains a link")
            if not path.is_file():
                raise ValueError("Workspace contains a non-file entry")
            files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(workspace).as_posix()):
        relative = path.relative_to(workspace).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def run_named_test(
    manifest: dict,
    name: str,
    workspace: Path,
    artifacts: Path,
    timeout_seconds: int = 120,
) -> dict:
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("Invalid named test identifier")
    command = manifest.get("named_tests", {}).get(name)
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError("Named test does not resolve to an approved command")
    if timeout_seconds < 1 or timeout_seconds > 300:
        raise ValueError("Test timeout is outside policy")
    artifacts.mkdir(parents=True, exist_ok=True)
    tested_tree_hash = tree_hash(workspace)
    started = datetime.now(UTC).isoformat()
    tick = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            errors="replace",
        )
        exit_code = completed.returncode
        status = "PASSED" if exit_code == 0 else "FAILED"
        output = completed.stdout + "\n" + completed.stderr
    except subprocess.TimeoutExpired as error:
        exit_code = None
        status = "TIMEOUT"
        output = f"Test timed out after {timeout_seconds} seconds: {error}"
    after_hash = tree_hash(workspace)
    if tested_tree_hash != after_hash:
        status = "INCONCLUSIVE"
        output += "\nWorkspace changed while the test was running."
    output_path = artifacts / f"test-{name}.log"
    output_path.write_text(output, encoding="utf-8")
    receipt = {
        "schema_version": "1.0",
        "case_id": manifest["case_id"],
        "fixture_revision": manifest.get("fixture_revision", manifest["oracle_revision"]),
        "test_name": name,
        "command": command,
        "tested_tree_sha256": tested_tree_hash,
        "post_test_tree_sha256": after_hash,
        "started_at": started,
        "duration_ms": int((time.monotonic() - tick) * 1000),
        "exit_code": exit_code,
        "status": status,
        "output_file": output_path.name,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }
    (artifacts / f"test-{name}.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def run_hidden_oracle(
    manifest: dict, workspace: Path, oracle: Path, artifacts: Path,
    timeout_seconds: int = 120,
) -> dict:
    """Run a trusted oracle outside the candidate tree for synthetic fixtures."""
    if not oracle.resolve(strict=True).is_file():
        raise ValueError("Oracle is missing")
    artifacts.mkdir(parents=True, exist_ok=True)
    tested_tree_hash = tree_hash(workspace)
    command = ["python", "-m", "pytest", "-q", str(oracle.resolve())]
    tick = time.monotonic()
    started = datetime.now(UTC).isoformat()
    try:
        completed = subprocess.run(
            command, cwd=workspace, env={**os.environ, "PYTHONPATH": str(workspace.resolve())},
            capture_output=True, text=True, errors="replace", timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        status = "PASSED" if exit_code == 0 else "FAILED"
        output = completed.stdout + "\n" + completed.stderr
    except subprocess.TimeoutExpired:
        exit_code = None
        status = "TIMEOUT"
        output = f"Oracle timed out after {timeout_seconds} seconds"
    after_hash = tree_hash(workspace)
    if after_hash != tested_tree_hash:
        status = "INCONCLUSIVE"
        output += "\nWorkspace changed while oracle ran."
    log = artifacts / "oracle.log"
    log.write_text(output, encoding="utf-8")
    receipt = {
        "schema_version": "1.0",
        "case_id": manifest["case_id"],
        "oracle_revision": manifest["oracle_revision"],
        "command": command,
        "tested_tree_sha256": tested_tree_hash,
        "post_test_tree_sha256": after_hash,
        "started_at": started,
        "duration_ms": int((time.monotonic() - tick) * 1000),
        "exit_code": exit_code,
        "status": status,
        "output_file": log.name,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }
    (artifacts / "oracle.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt
