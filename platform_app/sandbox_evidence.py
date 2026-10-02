"""Bounded guest-side evidence archive and root-side single-file reader."""

from __future__ import annotations

import io
import os
import stat
import tarfile
from pathlib import Path


class SandboxEvidenceError(ValueError):
    pass


def package_guest_evidence(artifacts: Path) -> Path:
    """Run as uid 10001 so tenant-controlled paths are never read as root."""
    destination = artifacts / "evidence.tar"
    total = 0
    count = 0
    try:
        with destination.open("xb") as stream, tarfile.open(fileobj=stream, mode="w") as tar:
            for path in sorted(artifacts.rglob("*")):
                if path == destination:
                    continue
                if path.is_symlink():
                    raise SandboxEvidenceError("Guest evidence contains a link")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise SandboxEvidenceError("Guest evidence contains a special file")
                count += 1
                size = path.stat().st_size
                total += size
                if count > 1000 or size > 20_000_000 or total > 45_000_000:
                    raise SandboxEvidenceError("Guest evidence exceeds policy")
                entry = tarfile.TarInfo(path.relative_to(artifacts).as_posix())
                entry.size = size
                entry.mode = 0o644
                entry.mtime = 0
                with path.open("rb") as source:
                    tar.addfile(entry, source)
        if destination.stat().st_size > 50_000_000:
            raise SandboxEvidenceError("Guest evidence archive exceeds policy")
    except OSError as error:
        raise SandboxEvidenceError("Guest evidence could not be packaged") from error
    return destination


def read_guest_evidence(artifacts: Path) -> bytes:
    """Root reads one no-follow descriptor, never a guest-controlled tree."""
    path = artifacts / "evidence.tar"
    if os.name == "posix" and not hasattr(os, "O_NOFOLLOW"):
        raise SandboxEvidenceError("Guest platform lacks no-follow file access")
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
    except FileNotFoundError:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w"):
            pass
        return output.getvalue()
    except OSError as error:
        raise SandboxEvidenceError("Guest evidence file is inaccessible") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= 50_000_000:
            raise SandboxEvidenceError("Guest evidence file is not bounded and regular")
        parts = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise SandboxEvidenceError("Guest evidence file changed while reading")
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)
    finally:
        os.close(descriptor)
