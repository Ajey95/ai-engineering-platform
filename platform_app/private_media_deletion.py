"""Revoke and verify private S3/CloudFront recording cleanup from the outbox."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.artifact_quota import delete_artifact_charge
from platform_app.config import Settings
from platform_app.model_base import utcnow
from platform_app.models import (
    OutboxEvent,
    PrivateMediaPublication,
    Run,
)
from platform_app.private_media import media_prefix
from platform_app.recording_deletion import deletion_for, purge_local_recording
from platform_app.service import ServiceError, append_event

_DISTRIBUTION = re.compile(r"[A-Z0-9]{5,30}\Z")


def _purge_prefix(s3, bucket: str, prefix: str) -> None:
    for _ in range(10):
        page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objects = page.get("Contents", [])
        if not objects:
            return
        if len(objects) > 1000:
            raise ServiceError("MEDIA_DELETE_LIMIT", "Private media prefix exceeds limit", 503)
        keys = [item["Key"] for item in objects]
        if any(not key.startswith(prefix) for key in keys):
            raise ServiceError("MEDIA_DELETE_SCOPE", "Object listing crossed scope", 503)
        response = s3.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True}
        )
        if response.get("Errors"):
            raise ServiceError("MEDIA_DELETE_RETRY", "Private media deletion failed", 503)
    if s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents"):
        raise ServiceError("MEDIA_DELETE_RETRY", "Private media deletion is incomplete", 503)


def process_private_media_deletion(
    db: Session, config: Settings, event: OutboxEvent, s3, cloudfront,
) -> bool:
    """Return true only after S3 is empty and CloudFront invalidation completes."""
    if event.topic != "private_media.delete" or event.status == "delivered":
        raise ServiceError("MEDIA_DELETE_EVENT", "Unexpected deletion event", 409)
    run_id, label = event.payload.get("run_id"), event.payload.get("label")
    if not isinstance(run_id, str) or label not in {"baseline", "candidate"}:
        raise ServiceError("MEDIA_DELETE_EVENT", "Deletion payload is invalid", 409)
    run = db.scalar(select(Run).where(
        Run.id == run_id, Run.tenant_id == event.tenant_id
    ).with_for_update())
    if run is None:
        raise ServiceError("MEDIA_DELETE_SCOPE", "Deletion run is unavailable", 409)
    deletion = deletion_for(db, run, label)
    publication = db.scalar(select(PrivateMediaPublication).where(
        PrivateMediaPublication.tenant_id == run.tenant_id,
        PrivateMediaPublication.project_id == run.project_id,
        PrivateMediaPublication.run_id == run.id,
        PrivateMediaPublication.label == label,
    ))
    if deletion is None or publication is None:
        raise ServiceError("MEDIA_DELETE_SCOPE", "Deletion record is incomplete", 409)
    if publication.status == "deleted" and deletion.status == "complete":
        event.status = "delivered"
        return True
    if (
        not config.private_media_bucket
        or not _DISTRIBUTION.fullmatch(config.cloudfront_distribution_id)
    ):
        raise ServiceError("MEDIA_DELETE_CONFIG", "Private delivery is not configured", 503)
    try:
        prefix = media_prefix(
            run.tenant_id, run.project_id, run.id, label, publication.effect_hash
        )
    except ValueError as error:
        raise ServiceError("MEDIA_DELETE_SCOPE", "Publication scope is invalid", 409) from error
    _purge_prefix(s3, config.private_media_bucket, prefix)
    invalidation = cloudfront.create_invalidation(
        DistributionId=config.cloudfront_distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": [f"/{prefix}*"]},
            "CallerReference": deletion.id,
        },
    )["Invalidation"]
    status = cloudfront.get_invalidation(
        DistributionId=config.cloudfront_distribution_id, Id=invalidation["Id"]
    )["Invalidation"]["Status"]
    if status != "Completed":
        event.attempts += 1
        return False
    purge_local_recording(db, run, label, config.artifact_dir)
    publication.status = "deleted"
    delete_artifact_charge(db, run, "private_media", label)
    deletion.status = "complete"
    deletion.completed_at = utcnow()
    other = "candidate" if label == "baseline" else "baseline"
    run.media_status = "DELETED" if deletion_for(db, run, other) else "PARTIALLY_DELETED"
    append_event(db, run, "artifact.deleted", {
        "label": label, "remote_prefix": prefix, "edge_invalidation": invalidation["Id"],
    })
    event.status = "delivered"
    return True
