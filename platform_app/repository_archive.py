"""Package one exact authorized Git commit without passing Git credentials to a guest."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from platform_app.safe_archive import UnsafeArchive, extract_regular_tar


class RepositoryArchiveError(ValueError):
    pass


_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


@dataclass(frozen=True)
class SourceArchive:
    commit: str
    sha256: str
    archive: bytes
    file_count: int


def archive_repository_commit(
    repository: Path, commit: str, *, max_archive_bytes: int = 50_000_000
) -> SourceArchive:
    if not _COMMIT.fullmatch(commit) or not 1 <= max_archive_bytes <= 50_000_000:
        raise RepositoryArchiveError("Pinned commit or archive limit is invalid")
    try:
        repository = repository.resolve(strict=True)
    except OSError as error:
        raise RepositoryArchiveError("Repository checkout is unavailable") from error
    if not repository.is_dir():
        raise RepositoryArchiveError("Repository checkout is unavailable")
    try:
        resolved = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--verify", f"{commit}^{{commit}}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RepositoryArchiveError("Pinned commit cannot be verified") from error
    if resolved.returncode or resolved.stdout.strip() != commit:
        raise RepositoryArchiveError("Pinned commit does not resolve exactly")
    process = None
    try:
        process = subprocess.Popen(
            ["git", "-C", str(repository), "archive", "--format=tar", commit],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if process.stdout is None:
            raise RepositoryArchiveError("Repository archive pipe is unavailable")
        raw = process.stdout.read(max_archive_bytes + 1)
        if len(raw) > max_archive_bytes:
            process.kill()
            process.communicate(timeout=5)
            raise RepositoryArchiveError("Repository archive exceeds policy")
        _, stderr = process.communicate(timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        raise RepositoryArchiveError("Repository archive failed") from error
    if process.returncode or stderr:
        raise RepositoryArchiveError("Repository archive failed")
    try:
        with tempfile.TemporaryDirectory(prefix="aip-archive-check-") as temporary:
            root = Path(temporary)
            extract_regular_tar(
                raw, Path(temporary), max_archive_bytes=max_archive_bytes
            )
            files = sum(path.is_file() for path in root.rglob("*"))
    except UnsafeArchive as error:
        raise RepositoryArchiveError("Repository archive contains unsafe entries") from error
    if files == 0:
        raise RepositoryArchiveError("Repository archive is empty")
    return SourceArchive(
        commit=commit, sha256=hashlib.sha256(raw).hexdigest(),
        archive=raw, file_count=files,
    )
