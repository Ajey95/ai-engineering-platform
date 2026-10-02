"""Independent, replayable hosted WebM to private HLS publication worker."""

from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_, select

from platform_app.config import Settings
from platform_app.db import utcnow
from platform_app.media import MediaError, encode_hls, sha256_file
from platform_app.models import OutboxEvent, Run, SandboxLease, ToolAction
from platform_app.private_media_store import publish_recording
from platform_app.recording_deletion import deletion_for
from platform_app.service import ServiceError, canonical_hash


def _finish(session_factory, event_id: str, token: str, status: str, error: str | None) -> str:
    with session_factory() as db:
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.id == event_id,
            OutboxEvent.topic == "media.transcode",
        ).with_for_update())
        if event is None or event.processing_token != token:
            return event_id
        if status == "pending" and event.attempts >= 5:
            status = "failed"
        event.status = status
        event.processing_token = None
        event.processing_lease_until = None
        if error:
            event.payload = {**event.payload, "last_error_code": error}
        elif "last_error_code" in event.payload:
            event.payload = {
                key: value for key, value in event.payload.items()
                if key != "last_error_code"
            }
        if status == "failed":
            run = db.get(Run, event.payload.get("run_id"))
            if run and run.tenant_id == event.tenant_id:
                run.media_status = "FAILED"
        db.commit()
    return event_id


def _scoped_source(root: Path, run: Run, event: OutboxEvent) -> Path:
    payload = event.payload
    label = payload.get("label")
    if label not in {"baseline", "candidate"} or payload.get("project_id") != run.project_id:
        raise ServiceError("MEDIA_SOURCE_INVALID", "Media scope changed", 409)
    path = root / "hosted-media" / run.tenant_id / run.id / label / "source.webm"
    if (
        not path.resolve().is_relative_to(root)
        or path.is_symlink() or path.parent.is_symlink()
        or not path.is_file() or path.stat().st_size != payload.get("source_bytes")
        or path.stat().st_size > 20_000_000
        or sha256_file(path) != payload.get("source_sha256")
    ):
        raise ServiceError("MEDIA_SOURCE_INVALID", "Staged recording changed", 409)
    return path


def dispatch_one_hosted_media(
    session_factory, config: Settings, artifact_root: Path, s3,
    *, event_id: str | None = None,
) -> str | None:
    """Lease one job; encode and S3 publication are idempotent after a crash."""
    root = Path(artifact_root).resolve()
    with session_factory() as db:
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.topic == "media.transcode",
            *([OutboxEvent.id == event_id] if event_id is not None else []),
            or_(
                OutboxEvent.status == "pending",
                (OutboxEvent.status == "processing") &
                (OutboxEvent.processing_lease_until <= utcnow()),
            ),
        ).order_by(OutboxEvent.created_at, OutboxEvent.id)
            .with_for_update(skip_locked=True).limit(1))
        if event is None:
            return None
        event_id = event.id
        token = str(uuid4())
        event.status = "processing"
        event.attempts += 1
        event.processing_token = token
        event.processing_lease_until = utcnow() + timedelta(hours=1)
        db.commit()
    try:
        deleted = False
        with session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            payload = event.payload
            run = db.scalar(select(Run).where(
                Run.id == payload.get("run_id"),
                Run.tenant_id == event.tenant_id,
                Run.project_id == payload.get("project_id"),
            ))
            if run is None:
                raise ServiceError("MEDIA_SOURCE_INVALID", "Media run is unavailable", 409)
            label = payload.get("label")
            if deletion_for(db, run, label) is not None:
                deleted = True
            if deleted:
                source = None
            else:
                lease = db.get(SandboxLease, payload.get("lease_id"))
                if (
                    lease is None or lease.tenant_id != run.tenant_id
                    or lease.project_id != run.project_id or lease.run_id != run.id
                    or lease.phase != label or lease.result_received_at is None
                    or (lease.result_summary or {}).get("evidence_sha256")
                    != payload.get("evidence_sha256")
                ):
                    raise ServiceError("MEDIA_SOURCE_INVALID", "Guest receipt changed", 409)
                source = _scoped_source(root, run, event)
                arguments = canonical_hash({
                    "label": label, "source_sha256": payload["source_sha256"],
                    "profile_revision": "hls-v1",
                })
                action = db.scalar(select(ToolAction).where(
                    ToolAction.tenant_id == run.tenant_id,
                    ToolAction.run_id == run.id,
                    ToolAction.step_id == f"media_{label}",
                ).with_for_update())
                if action is None:
                    action = ToolAction(
                        tenant_id=run.tenant_id, run_id=run.id,
                        step_id=f"media_{label}", logical_action="media.encode",
                        effect_key=canonical_hash({"run_id": run.id, "label": label}),
                        arguments_hash=arguments, policy_result="approved",
                        status="INTENDED",
                    )
                    db.add(action)
                elif action.arguments_hash != arguments or action.logical_action != "media.encode":
                    raise ServiceError("MEDIA_SOURCE_CONFLICT", "Media action changed", 409)
                tenant_id, run_id = run.tenant_id, run.id
                db.commit()
        if deleted:
            return _finish(session_factory, event_id, token, "delivered", None)
        master = encode_hls(
            source, root / "private-media", tenant_id, f"{run_id}_{label}",
        )
        with session_factory() as db:
            event = db.scalar(select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.processing_token == token,
            ).with_for_update())
            if event is None:
                raise ServiceError("MEDIA_LEASE_LOST", "Media job lease was lost", 409)
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if deletion_for(db, run, label) is not None:
                db.rollback()
                return _finish(session_factory, event_id, token, "delivered", None)
            action = db.scalar(select(ToolAction).where(
                ToolAction.tenant_id == tenant_id, ToolAction.run_id == run_id,
                ToolAction.step_id == f"media_{label}",
            ).with_for_update())
            action.status = "COMPLETED"
            action.completed_at = utcnow()
            action.receipt = {
                "status": "READY", "label": label,
                "master": str(master.relative_to(root)),
                "source_sha256": event.payload["source_sha256"],
            }
            db.commit()
        with session_factory() as db:
            run = db.get(Run, run_id)
            publish_recording(db, config, run, label, root, s3)
            jobs = db.scalars(select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.topic == "media.transcode",
            )).all()
            other = [
                job for job in jobs
                if job.id != event_id and job.payload.get("run_id") == run_id
            ]
            run.media_status = (
                "FAILED" if any(job.status == "failed" for job in other) else
                "PROCESSING" if any(
                    job.status in {"pending", "processing"} for job in other
                ) else "READY"
            )
            db.commit()
        return _finish(session_factory, event_id, token, "delivered", None)
    except (ServiceError, MediaError, OSError, subprocess.TimeoutExpired) as error:
        code = error.code if isinstance(error, ServiceError) else type(error).__name__
        permanent = code in {"MEDIA_SOURCE_INVALID", "MEDIA_SOURCE_CONFLICT"}
        return _finish(
            session_factory, event_id, token,
            "failed" if permanent else "pending", code,
        )
