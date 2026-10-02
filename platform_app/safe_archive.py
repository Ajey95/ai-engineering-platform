"""Extract a bounded regular-file tar archive into a new workspace."""

from __future__ import annotations

import io
import os
import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath


class UnsafeArchive(ValueError):
    pass


_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def extract_regular_tar(
    data: bytes,
    destination: Path,
    *,
    permitted: Callable[[str], bool] | None = None,
    max_archive_bytes: int = 50_000_000,
    max_files: int = 10_000,
    max_file_bytes: int = 20_000_000,
    max_expanded_bytes: int = 200_000_000,
) -> list[str]:
    """Reject links, devices, traversal, collisions and archive expansion bombs."""
    if not data or len(data) > max_archive_bytes:
        raise UnsafeArchive("Archive size is outside policy")
    if destination.is_symlink() or os.path.isjunction(destination):
        raise UnsafeArchive("Extraction destination is linked")
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve(strict=True)
    if any(destination.iterdir()):
        raise UnsafeArchive("Extraction destination must be empty")
    members: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    seen: set[str] = set()
    files = 0
    expanded = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as source:
            for member in source:
                name = member.name
                path = PurePosixPath(name)
                if (
                    not name or "\\" in name or ":" in name
                    or path.is_absolute() or ".." in path.parts
                    or name != path.as_posix().rstrip("/")
                    or any(
                        part.endswith((".", " "))
                        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
                        for part in path.parts
                    )
                    or (permitted is not None and not permitted(path.as_posix()))
                    or not (member.isfile() or member.isdir())
                    or bool(getattr(member, "sparse", None))
                ):
                    raise UnsafeArchive("Archive contains an unsafe entry")
                canonical = path.as_posix().casefold()
                if canonical in seen:
                    raise UnsafeArchive("Archive has duplicate or ambiguous paths")
                seen.add(canonical)
                if member.isfile():
                    files += 1
                    expanded += member.size
                    if member.size < 0 or member.size > max_file_bytes:
                        raise UnsafeArchive("Archive file exceeds policy")
                    if files > max_files or expanded > max_expanded_bytes:
                        raise UnsafeArchive("Archive expansion exceeds policy")
                members.append((member, path))
            names = {path.as_posix().casefold(): member for member, path in members}
            for member, path in members:
                for parent in path.parents:
                    if parent == PurePosixPath("."):
                        break
                    prior = names.get(parent.as_posix().casefold())
                    if prior is not None and prior.isfile():
                        raise UnsafeArchive("A file is used as a directory")
            for member, path in members:
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination):
                    raise UnsafeArchive("Archive target escaped workspace")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = source.extractfile(member)
                if stream is None:
                    raise UnsafeArchive("Archive file data is unavailable")
                with target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
                if target.stat().st_size != member.size:
                    raise UnsafeArchive("Archive file size changed during extraction")
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, OSError, EOFError) as error:
        raise UnsafeArchive("Archive is invalid or incomplete") from error
    return [path.as_posix() for _, path in members]
