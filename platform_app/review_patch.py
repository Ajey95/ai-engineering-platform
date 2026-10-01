"""Reconstruct a reviewable diff from a pinned fixture and verified candidate."""

from __future__ import annotations

import difflib
import json
import tempfile
from pathlib import Path

from platform_app.development_worker import _pinned_fixture
from platform_app.models import Run
from platform_app.patch_workspace import PatchError, parse_patch_response
from platform_app.service import ServiceError
from platform_app.verifier import tree_hash


def verified_fixture_diff(
    run: Run, patch_receipt: dict, artifact_root: Path, repository: Path
) -> str:
    if run.config_snapshot.get("reproduction", {}).get("fixture_case_id") != "form-submit-001":
        raise ServiceError("PATCH_UNAVAILABLE", "Only the trusted fixture can be exported", 404)
    if patch_receipt.get("status") != "COMPLETED" or patch_receipt.get("changed_files") != [
        "server.py"
    ]:
        raise ServiceError("PATCH_UNAVAILABLE", "Patch receipt is incomplete", 404)
    candidate = artifact_root / run.id / "candidate" / "workspace"
    source = candidate / "server.py"
    if not source.is_file() or source.is_symlink():
        raise ServiceError("PATCH_UNAVAILABLE", "Candidate source is unavailable", 404)
    try:
        if tree_hash(candidate) != patch_receipt.get("candidate_tree_sha256"):
            raise ValueError("Candidate tree changed")
        content = source.read_text(encoding="utf-8")
        proposal = parse_patch_response(json.dumps({
            "diagnosis": "Verified candidate",
            "files": [{"path": "server.py", "content": content}],
        }))
        if proposal.patch_sha256 != patch_receipt.get("patch_sha256"):
            raise ValueError("Candidate patch changed")
    except (OSError, UnicodeError, ValueError, PatchError) as error:
        raise ServiceError(
            "PATCH_CHANGED", "Candidate no longer matches its receipt", 409
        ) from error
    with tempfile.TemporaryDirectory(prefix="aip-review-patch-") as temporary:
        _, baseline, _ = _pinned_fixture(repository, run.base_commit, Path(temporary))
        before = (baseline / "server.py").read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/server.py@{run.base_commit}",
        tofile="b/server.py",
    ))
    if not diff:
        raise ServiceError("PATCH_CHANGED", "Candidate has no source diff", 409)
    return diff
