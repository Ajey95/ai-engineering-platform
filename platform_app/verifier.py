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


def _output_receipt(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


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
    output_path = artifacts / f"test-{name}.log"
    with output_path.open("wb") as output:
        try:
            completed = subprocess.run(
                command, cwd=workspace, stdout=output, stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
            exit_code = completed.returncode
            status = "PASSED" if exit_code == 0 else "FAILED"
        except subprocess.TimeoutExpired:
            exit_code = None
            status = "TIMEOUT"
            output.write(f"\nTest timed out after {timeout_seconds} seconds\n".encode())
    after_hash = tree_hash(workspace)
    if tested_tree_hash != after_hash:
        status = "INCONCLUSIVE"
        with output_path.open("ab") as output:
            output.write(b"\nWorkspace changed while the test was running.\n")
    output_sha256, output_bytes = _output_receipt(output_path)
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
        "output_sha256": output_sha256,
        "output_bytes": output_bytes,
    }
    (artifacts / f"test-{name}.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def run_hidden_oracle(
    manifest: dict,
    workspace: Path,
    oracle: Path,
    artifacts: Path,
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
    log = artifacts / "oracle.log"
    with log.open("wb") as output:
        try:
            completed = subprocess.run(
                command, cwd=workspace,
                env={**os.environ, "PYTHONPATH": str(workspace.resolve())},
                stdout=output, stderr=subprocess.STDOUT, timeout=timeout_seconds,
            )
            exit_code = completed.returncode
            status = "PASSED" if exit_code == 0 else "FAILED"
        except subprocess.TimeoutExpired:
            exit_code = None
            status = "TIMEOUT"
            output.write(f"\nOracle timed out after {timeout_seconds} seconds\n".encode())
    after_hash = tree_hash(workspace)
    if after_hash != tested_tree_hash:
        status = "INCONCLUSIVE"
        with log.open("ab") as output:
            output.write(b"\nWorkspace changed while oracle ran.\n")
    output_sha256, output_bytes = _output_receipt(log)
    receipt = {
        "schema_version": "1.0",
        "case_id": manifest["case_id"],
        "oracle_revision": manifest["oracle_revision"],
        "oracle_sha256": hashlib.sha256(oracle.read_bytes()).hexdigest(),
        "command": command,
        "tested_tree_sha256": tested_tree_hash,
        "post_test_tree_sha256": after_hash,
        "started_at": started,
        "duration_ms": int((time.monotonic() - tick) * 1000),
        "exit_code": exit_code,
        "status": status,
        "output_file": log.name,
        "output_sha256": output_sha256,
        "output_bytes": output_bytes,
    }
    (artifacts / "oracle.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt
