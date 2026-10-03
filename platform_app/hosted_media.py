"""Stage verified guest recordings for independent private HLS publication."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from platform_app.models import OutboxEvent, Run, SandboxLease
from platform_app.recording_deletion import deletion_for
from platform_app.run_ledger import assert_fence
from platform_app.safe_archive import UnsafeArchive, extract_regular_tar
from platform_app.sandbox_transport import GuestOutput
from platform_app.service import ServiceError, append_event
from platform_app.telemetry import inject_trace

_RECORDING = re.compile(r"[A-Za-z0-9_-]{1,80}\.webm\Z")


def media_event_id(run_id: str, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"aip-hosted-media:{run_id}:{label}"))


def staged_recording_path(root: Path, run: Run, label: str) -> Path:
    if label not in {"baseline", "candidate"}:
        raise ValueError("Unknown recording label")
    return root.resolve() / "hosted-media" / run.tenant_id / run.id / label / "source.webm"


def stage_hosted_recording(
    db: Session, run: Run, worker_id: str, fence: int,
    output: GuestOutput, label: str, artifact_root: Path,
) -> bool:
    """Commit a media intent only after verified guest bytes are durable."""
    assert_fence(run, worker_id, fence)
    if label not in {"baseline", "candidate"}:
        raise ValueError("Unknown recording label")
    receipt = output.result
    browser = (receipt.get(label) or {}).get("browser") or {}
    recording = browser.get("recording")
    if recording is None and browser.get("recording_disabled_reason"):
        return False
    if not isinstance(recording, str) or not _RECORDING.fullmatch(recording):
        return False
    if deletion_for(db, run, label) is not None:
        return False
    lease = db.get(SandboxLease, receipt.get("lease_id"))
    if (
        lease is None or lease.run_id != run.id or lease.tenant_id != run.tenant_id
        or lease.project_id != run.project_id or lease.phase != label
        or lease.result_received_at is None
        or hashlib.sha256(output.evidence_archive).hexdigest()
        != receipt.get("evidence_sha256")
    ):
        raise ServiceError("MEDIA_SOURCE_INVALID", "Guest evidence is not pinned", 409)
    root = artifact_root.resolve()
    work = root / "_trusted_work"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aip-media-stage-", dir=work) as temporary:
        directory = Path(temporary)
        try:
            names = extract_regular_tar(
                output.evidence_archive, directory,
                max_archive_bytes=50_000_000, max_files=1000,
                max_file_bytes=20_000_000, max_expanded_bytes=45_000_000,
            )
        except UnsafeArchive as error:
            raise ServiceError("MEDIA_SOURCE_INVALID", "Guest archive is invalid", 409) from error
        if f"browser/{recording}" not in names:
            raise ServiceError("MEDIA_SOURCE_INVALID", "Guest recording is absent", 409)
        raw = (directory / "browser" / recording).read_bytes()
    if not raw or len(raw) > 20_000_000:
        raise ServiceError("MEDIA_SOURCE_INVALID", "Guest recording exceeds policy", 409)
    digest = hashlib.sha256(raw).hexdigest()
    target = staged_recording_path(root, run, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    if (
        target.is_symlink() or target.parent.is_symlink()
        or not target.resolve().is_relative_to(root)
    ):
        raise ServiceError("MEDIA_SOURCE_INVALID", "Recording target is linked", 409)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ServiceError("MEDIA_SOURCE_CONFLICT", "Recording changed after staging", 409)
    else:
        temporary = target.parent / ".source.webm.tmp"
        temporary.write_bytes(raw)
        os.replace(temporary, target)
    event_id = media_event_id(run.id, label)
    payload = {
        "run_id": run.id, "project_id": run.project_id, "label": label,
        "lease_id": lease.id, "evidence_sha256": receipt["evidence_sha256"],
        "source_sha256": digest, "source_bytes": len(raw),
    }
    event = db.get(OutboxEvent, event_id)
    if event is None:
        db.add(OutboxEvent(
            id=event_id, tenant_id=run.tenant_id,
            topic="media.transcode", payload={**payload, **inject_trace()},
        ))
        append_event(db, run, "media.queued", {"label": label, "source_sha256": digest})
        run.media_status = "PROCESSING"
    elif event.tenant_id != run.tenant_id or any(
        event.payload.get(key) != value for key, value in payload.items()
    ):
        raise ServiceError("MEDIA_SOURCE_CONFLICT", "Media job changed", 409)
    return True
