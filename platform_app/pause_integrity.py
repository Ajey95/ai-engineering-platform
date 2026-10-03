"""Canonical receipt checkpoint for a run that has released its worker lease."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import Run, RunEvent, SandboxLease, ToolAction
from platform_app.service import ServiceError, canonical_hash


def pause_checkpoint(db: Session, run: Run) -> dict:
    actions = db.scalars(select(ToolAction).where(
        ToolAction.tenant_id == run.tenant_id,
        ToolAction.run_id == run.id,
        ToolAction.status == "COMPLETED",
    ).order_by(ToolAction.id)).all()
    guests = db.scalars(select(SandboxLease).where(
        SandboxLease.tenant_id == run.tenant_id,
        SandboxLease.run_id == run.id,
        SandboxLease.result_sha256.is_not(None),
    ).order_by(SandboxLease.id)).all()
    return {
        "run_sha256": canonical_hash({
            "tenant_id": run.tenant_id, "project_id": run.project_id,
            "task_id": run.task_id, "base_commit": run.base_commit,
            "model_entry_id": run.model_entry_id,
            "resume_target": run.resume_target,
            "config_snapshot": run.config_snapshot,
        }),
        "completed_actions": [{
            "id": action.id,
            "sha256": canonical_hash({
                "effect_key": action.effect_key,
                "arguments_hash": action.arguments_hash,
                "receipt": action.receipt,
            }),
        } for action in actions],
        "guest_results": [{
            "id": lease.id,
            "sha256": canonical_hash({
                "source_sha256": lease.source_sha256,
                "result_sha256": lease.result_sha256,
                "result_summary": lease.result_summary,
            }),
        } for lease in guests],
    }


def pause_event_payload(db: Session, run: Run) -> dict:
    checkpoint = pause_checkpoint(db, run)
    return {
        "state": run.state,
        "verdict": run.verdict,
        "checkpoint": checkpoint,
        "checkpoint_sha256": canonical_hash(checkpoint),
    }


def verify_pause_checkpoint(db: Session, run: Run) -> None:
    paused = db.scalar(select(RunEvent).where(
        RunEvent.tenant_id == run.tenant_id,
        RunEvent.run_id == run.id,
        RunEvent.event_type == "run.state_changed",
    ).order_by(RunEvent.sequence.desc()).limit(1))
    payload = paused.payload if paused is not None else {}
    checkpoint = payload.get("checkpoint") if isinstance(payload, dict) else None
    if (
        payload.get("state") != run.state
        or not isinstance(checkpoint, dict)
        or payload.get("checkpoint_sha256") != canonical_hash(checkpoint)
    ):
        raise ServiceError("PAUSE_CHECKPOINT_INVALID", "Pause checkpoint is unavailable", 409)
    current = pause_checkpoint(db, run)
    if checkpoint.get("run_sha256") != current["run_sha256"]:
        raise ServiceError("PAUSE_CHECKPOINT_CHANGED", "Pinned run plan changed", 409)
    for key in ("completed_actions", "guest_results"):
        expected = checkpoint.get(key)
        if not isinstance(expected, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("sha256"), str)
            for item in expected
        ):
            raise ServiceError("PAUSE_CHECKPOINT_INVALID", "Pause receipts are invalid", 409)
        observed = {item["id"]: item["sha256"] for item in current[key]}
        if any(observed.get(item["id"]) != item["sha256"] for item in expected):
            raise ServiceError("PAUSE_CHECKPOINT_CHANGED", "Completed evidence changed", 409)
