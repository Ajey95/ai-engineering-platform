"""Bounded full-file repair proposals against an exact pinned source archive."""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import re
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from platform_app.repository_archive import SourceArchive
from platform_app.safe_archive import UnsafeArchive, extract_regular_tar
from platform_app.verifier import tree_hash


class GeneralPatchError(ValueError):
    pass


_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ProposedFile:
    path: str
    base_sha256: str
    content: str


@dataclass(frozen=True)
class GeneralPatch:
    diagnosis: str
    files: tuple[ProposedFile, ...]
    patch_sha256: str


@dataclass(frozen=True)
class CandidateTree:
    source: SourceArchive
    tree_sha256: str
    diff: str


def parse_general_patch(raw: str, allowed_paths: frozenset[str]) -> GeneralPatch:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 200_000:
        raise GeneralPatchError("Repair proposal exceeds policy")
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise GeneralPatchError("Repair proposal is not JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"diagnosis", "files"}:
        raise GeneralPatchError("Repair proposal fields are invalid")
    diagnosis = payload["diagnosis"]
    entries = payload["files"]
    if not isinstance(diagnosis, str) or not 1 <= len(diagnosis.strip()) <= 4000:
        raise GeneralPatchError("Repair diagnosis is invalid")
    if not isinstance(entries, list) or len(entries) > 4:
        raise GeneralPatchError("Repair must change at most four files")
    files: list[ProposedFile] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path", "base_sha256", "content"
        }:
            raise GeneralPatchError("Repair file entry is invalid")
        path, base, content = entry["path"], entry["base_sha256"], entry["content"]
        if (
            not isinstance(path, str) or path not in allowed_paths
            or path in seen or path.startswith("/") or "\\" in path
            or ":" in path or ".." in PurePosixPath(path).parts
            or path != PurePosixPath(path).as_posix()
        ):
            raise GeneralPatchError("Repair path is outside the approved scope")
        if not isinstance(base, str) or not _SHA.fullmatch(base):
            raise GeneralPatchError("Repair base digest is invalid")
        if (
            not isinstance(content, str) or "\x00" in content
            or len(content.encode("utf-8")) > 50_000
        ):
            raise GeneralPatchError("Repair file content exceeds policy")
        seen.add(path)
        files.append(ProposedFile(path, base, content))
    ordered = tuple(sorted(files, key=lambda item: item.path))
    canonical = [{
        "path": item.path, "base_sha256": item.base_sha256,
        "content": item.content,
    } for item in ordered]
    digest = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    return GeneralPatch(diagnosis.strip(), ordered, digest)


def build_candidate_tree(
    baseline: SourceArchive, proposal: GeneralPatch, work_root: Path
) -> CandidateTree:
    """Apply only approved full-file replacements; never execute the source."""
    if not proposal.files:
        raise GeneralPatchError("Repair proposal contains no candidate")
    if hashlib.sha256(baseline.archive).hexdigest() != baseline.sha256:
        raise GeneralPatchError("Pinned source archive digest changed")
    work_root = work_root.resolve(strict=True)
    if not work_root.is_dir():
        raise GeneralPatchError("Trusted candidate work root is unavailable")
    with tempfile.TemporaryDirectory(prefix="aip-candidate-", dir=work_root) as temporary:
        root = Path(temporary).resolve(strict=True)
        if not root.is_relative_to(work_root):
            raise GeneralPatchError("Candidate work root changed")
        try:
            paths = extract_regular_tar(baseline.archive, root)
        except UnsafeArchive as error:
            raise GeneralPatchError("Pinned source archive is unsafe") from error
        if sum((root / path).is_file() for path in paths) != baseline.file_count:
            raise GeneralPatchError("Pinned source file count changed")
        diffs = []
        for item in proposal.files:
            target = root.joinpath(*PurePosixPath(item.path).parts)
            if not target.is_file() or target.is_symlink() or not target.is_relative_to(root):
                raise GeneralPatchError("Approved repair target is unavailable")
            before_raw = target.read_bytes()
            if len(before_raw) > 100_000 or hashlib.sha256(
                before_raw
            ).hexdigest() != item.base_sha256:
                raise GeneralPatchError("Repair base file changed or exceeds policy")
            try:
                before = before_raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise GeneralPatchError("Repair target is not UTF-8 text") from error
            if before == item.content:
                raise GeneralPatchError("Repair proposal makes no change")
            diffs.append("".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                item.content.splitlines(keepends=True),
                fromfile=f"a/{item.path}", tofile=f"b/{item.path}",
            )))
            target.write_bytes(item.content.encode("utf-8"))
        output = io.BytesIO()
        count = 0
        with tarfile.open(fileobj=output, mode="w") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_dir():
                    continue
                if not path.is_file() or path.is_symlink():
                    raise GeneralPatchError("Candidate contains a nonregular file")
                count += 1
                data = path.read_bytes()
                if count > 10_000 or len(data) > 20_000_000:
                    raise GeneralPatchError("Candidate file exceeds policy")
                entry = tarfile.TarInfo(path.relative_to(root).as_posix())
                entry.size = len(data)
                entry.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                entry.mtime = 0
                archive.addfile(entry, io.BytesIO(data))
        raw = output.getvalue()
        if len(raw) > 50_000_000 or count != baseline.file_count:
            raise GeneralPatchError("Candidate archive exceeds policy")
        candidate = SourceArchive(
            commit=baseline.commit,
            sha256=hashlib.sha256(raw).hexdigest(),
            archive=raw, file_count=count,
        )
        return CandidateTree(candidate, tree_hash(root), "".join(diffs))
