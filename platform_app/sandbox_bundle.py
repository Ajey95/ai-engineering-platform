"""Build a deterministic guest bundle from a verified Git archive and approved plan."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from platform_app.environment_manifest import EnvironmentManifest
from platform_app.repository_archive import SourceArchive


class SandboxBundleError(ValueError):
    pass


@dataclass(frozen=True)
class GuestBundle:
    archive: bytes
    sha256: str
    source_commit: str
    manifest_sha256: str


def _entry(name: str, content: bytes, mode: int = 0o644) -> tarfile.TarInfo:
    entry = tarfile.TarInfo(name)
    entry.size = len(content)
    entry.mode = mode
    entry.mtime = 0
    entry.uid = 0
    entry.gid = 0
    entry.uname = ""
    entry.gname = ""
    return entry


def build_guest_bundle(
    source: SourceArchive, manifest: EnvironmentManifest,
    *, max_bytes: int = 50_000_000,
) -> GuestBundle:
    if not 1 <= max_bytes <= 50_000_000 or len(source.archive) > max_bytes or hashlib.sha256(
        source.archive
    ).hexdigest() != source.sha256:
        raise SandboxBundleError("Pinned source archive is inconsistent")
    plan = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(plan) > 100_000:
        raise SandboxBundleError("Environment manifest exceeds policy")
    output = io.BytesIO()
    try:
        with tarfile.open(fileobj=io.BytesIO(source.archive), mode="r:") as original:
            with tarfile.open(fileobj=output, mode="w") as bundle:
                file_count = 0
                expanded = 0
                for member in original:
                    path = PurePosixPath(member.name)
                    if (
                        path.is_absolute() or ".." in path.parts
                        or not (member.isfile() or member.isdir())
                    ):
                        raise SandboxBundleError("Source archive is unsafe")
                    if member.isdir():
                        continue
                    file_count += 1
                    expanded += member.size
                    if (
                        member.size < 0 or member.size > 20_000_000
                        or file_count > 10_000 or expanded > 200_000_000
                    ):
                        raise SandboxBundleError("Source expansion exceeds policy")
                    reader = original.extractfile(member)
                    if reader is None:
                        raise SandboxBundleError("Source archive entry is missing")
                    content = reader.read(member.size + 1)
                    if len(content) != member.size:
                        raise SandboxBundleError("Source archive entry length changed")
                    mode = 0o755 if member.mode & 0o111 else 0o644
                    bundle.addfile(_entry(f"workspace/{path.as_posix()}", content, mode),
                                   io.BytesIO(content))
                if file_count != source.file_count:
                    raise SandboxBundleError("Source file count changed")
                bundle.addfile(_entry("control/manifest.json", plan), io.BytesIO(plan))
    except (tarfile.TarError, OSError) as error:
        raise SandboxBundleError("Guest bundle could not be built") from error
    raw = output.getvalue()
    if len(raw) > max_bytes:
        raise SandboxBundleError("Guest bundle exceeds policy")
    return GuestBundle(
        archive=raw, sha256=hashlib.sha256(raw).hexdigest(),
        source_commit=source.commit,
        manifest_sha256=hashlib.sha256(plan).hexdigest(),
    )
