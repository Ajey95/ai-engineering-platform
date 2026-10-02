"""Revocation and local object cleanup for one browser recording side."""

import shutil
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.model_base import utcnow
from platform_app.models import PrivateMediaPublication, RecordingDeletion, Run, ToolAction
from platform_app.service import append_event


def deletion_for(db: Session, run: Run, label: str) -> RecordingDeletion | None:
    return db.scalar(
        select(RecordingDeletion).where(
            RecordingDeletion.tenant_id == run.tenant_id,
            RecordingDeletion.run_id == run.id,
            RecordingDeletion.label == label,
        )
    )


def _scoped_path(root: Path, relative: Path) -> Path:
    """Reject links and junctions before removing a local artifact path."""
    selected = root / relative
    if selected.resolve() != selected.absolute() or not selected.resolve().is_relative_to(root):
        raise ValueError("Recording path crosses the artifact boundary")
    return selected


def purge_local_recording(db: Session, run: Run, label: str, artifact_dir: str) -> None:
    """Remove only this side's HLS and raw WebM; screenshots and ledger remain."""
    if label not in {"baseline", "candidate"}:
        raise ValueError("Invalid recording label")
    root = Path(artifact_dir).resolve()
    media = _scoped_path(
        root, Path("private-media") / run.tenant_id / f"{run.id}_{label}"
    )
    if media.exists():
        if not media.is_dir() or media.is_symlink() or media.is_junction():
            raise ValueError("Recording media target is not a regular directory")
        shutil.rmtree(media)

    step = "browser" if label == "baseline" else "candidate_browser"
    action = db.scalar(
        select(ToolAction).where(
            ToolAction.tenant_id == run.tenant_id,
            ToolAction.run_id == run.id,
            ToolAction.step_id == step,
            ToolAction.status == "COMPLETED",
        )
    )
    recording = (action.receipt or {}).get("recording") if action else None
    if (
        isinstance(recording, str)
        and recording.endswith(".webm")
        and Path(recording).name == recording
    ):
        evidence = (
            Path(run.id) / "baseline"
            if label == "baseline"
            else Path(run.id) / "candidate" / "evidence"
        )
        raw = _scoped_path(root, evidence / recording)
        if raw.exists():
            if not raw.is_file() or raw.is_symlink():
                raise ValueError("Raw recording target is not a regular file")
            raw.unlink()


def reconcile_local_recording_deletions(
    session_factory: Callable[[], Session], artifact_dir: str
) -> dict[str, int]:
    """Reapply durable tombstones before a restored development API serves media."""
    result = {"checked": 0, "cleaned": 0, "failed": 0}
    with session_factory() as db:
        deletions = db.scalars(
            select(RecordingDeletion).order_by(RecordingDeletion.created_at)
        ).all()
        for deletion in deletions:
            result["checked"] += 1
            run = db.get(Run, deletion.run_id)
            if run is None or run.tenant_id != deletion.tenant_id:
                deletion.status = "failed"
                result["failed"] += 1
                db.commit()
                continue
            pending_remote = db.scalar(select(PrivateMediaPublication.id).where(
                PrivateMediaPublication.tenant_id == deletion.tenant_id,
                PrivateMediaPublication.run_id == deletion.run_id,
                PrivateMediaPublication.label == deletion.label,
                PrivateMediaPublication.status == "ready",
            ))
            if pending_remote is not None:
                continue
            try:
                purge_local_recording(db, run, deletion.label, artifact_dir)
            except (OSError, ValueError):
                deletion.status = "failed"
                result["failed"] += 1
            else:
                if deletion.status != "complete":
                    append_event(db, run, "artifact.deleted", {"label": deletion.label})
                deletion.status = "complete"
                deletion.completed_at = utcnow()
                result["cleaned"] += 1
            db.commit()
    return result
