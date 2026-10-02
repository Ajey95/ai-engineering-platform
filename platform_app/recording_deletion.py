"""Revocation and local object cleanup for one browser recording side."""

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import RecordingDeletion, Run, ToolAction


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
