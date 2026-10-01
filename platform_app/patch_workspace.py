"""Validate and materialize bounded candidate patches for the trusted fixture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from platform_app.dev_sandbox import prepare_workspace
from platform_app.verifier import tree_hash

MAX_PATCH_BYTES = 100_000
MAX_FILE_BYTES = 50_000
ALLOWED_FIXTURE_FILES = frozenset({"server.py"})


class PatchError(Exception):
    pass


@dataclass(frozen=True)
class PatchProposal:
    diagnosis: str
    files: tuple[tuple[str, str], ...]
    patch_sha256: str


def parse_patch_response(
    raw: str, allowed_files: frozenset[str] = ALLOWED_FIXTURE_FILES
) -> PatchProposal:
    if len(raw.encode("utf-8")) > MAX_PATCH_BYTES:
        raise PatchError("Patch response exceeds policy")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PatchError("Patch response is not JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"diagnosis", "files"}:
        raise PatchError("Patch response fields are invalid")
    diagnosis = payload["diagnosis"]
    files = payload["files"]
    if not isinstance(diagnosis, str) or not diagnosis.strip() or len(diagnosis) > 4000:
        raise PatchError("Diagnosis is invalid")
    if not isinstance(files, list) or not 1 <= len(files) <= 4:
        raise PatchError("Patch must change one to four files")
    changes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise PatchError("Patch file entry is invalid")
        path, content = item["path"], item["content"]
        if not isinstance(path, str) or path not in allowed_files or path in seen:
            raise PatchError("Patch path is not authorized")
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise PatchError("Patch path escapes workspace")
        if (
            not isinstance(content, str)
            or "\x00" in content
            or len(content.encode("utf-8")) > MAX_FILE_BYTES
        ):
            raise PatchError("Patch content is invalid")
        seen.add(path)
        changes.append((path, content))
    normalized = {
        "diagnosis": diagnosis.strip(),
        "files": [{"path": path, "content": content} for path, content in sorted(changes)],
    }
    digest = hashlib.sha256(
        json.dumps(
            normalized["files"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return PatchProposal(normalized["diagnosis"], tuple(sorted(changes)), digest)


def materialize_candidate(source: Path, destination: Path, proposal: PatchProposal) -> str:
    prepare_workspace(source, destination)
    for name, content in proposal.files:
        target = destination / name
        if not target.is_file() or target.is_symlink():
            raise PatchError("Patch target is not an existing regular file")
        before = target.read_text(encoding="utf-8")
        if before == content:
            raise PatchError("Patch makes no change")
        target.write_text(content, encoding="utf-8", newline="\n")
    return tree_hash(destination)
