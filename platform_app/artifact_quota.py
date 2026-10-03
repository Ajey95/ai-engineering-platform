"""Durable tenant byte liability for private object publication."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import ArtifactCharge, AuditEvent, Run
from platform_app.service import ServiceError, canonical_hash
from platform_app.tenant_quota import QuotaError, lock_tenant


def artifact_usage_bytes(db: Session, tenant_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(ArtifactCharge.byte_count), 0)).where(
        ArtifactCharge.tenant_id == tenant_id,
        ArtifactCharge.status.in_(["reserved", "active"]),
    )) or 0)


def reserve_artifact(
    db: Session, run_id: str, kind: str, logical_key: str,
    sha256: str, byte_count: int,
) -> ArtifactCharge:
    if (
        kind != "private_media" or logical_key not in {"baseline", "candidate"}
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or type(byte_count) is not int or not 0 < byte_count <= 500_000_000
    ):
        raise ServiceError("ARTIFACT_INVALID", "Artifact reservation is invalid", 409)
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise ServiceError("ARTIFACT_INVALID", "Artifact run is missing", 409)
    try:
        tenant = lock_tenant(db, run.tenant_id)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 403) from error
    prior = db.scalar(select(ArtifactCharge).where(
        ArtifactCharge.tenant_id == tenant.id,
        ArtifactCharge.run_id == run.id,
        ArtifactCharge.kind == kind,
        ArtifactCharge.logical_key == logical_key,
    ).with_for_update())
    if prior is not None:
        if (
            prior.sha256 != sha256 or prior.byte_count != byte_count
            or prior.status == "deleted"
        ):
            raise ServiceError("ARTIFACT_CONFLICT", "Artifact reservation changed", 409)
        return prior
    used = artifact_usage_bytes(db, tenant.id)
    if used + byte_count > tenant.artifact_cap_bytes:
        raise ServiceError("ARTIFACT_QUOTA_EXHAUSTED", "Tenant artifact-byte cap reached", 409)
    charge = ArtifactCharge(
        tenant_id=tenant.id, run_id=run.id, kind=kind, logical_key=logical_key,
        sha256=sha256, byte_count=byte_count, status="reserved",
    )
    db.add(charge)
    db.flush()
    if used < tenant.artifact_cap_bytes * 0.8 <= used + byte_count:
        db.add(AuditEvent(
            tenant_id=tenant.id, actor="media-worker", action="artifact.threshold_80",
            target_ref=run.id,
            arguments_hash=canonical_hash({
                "run_id": run.id, "kind": kind, "logical_key": logical_key,
                "sha256": sha256, "byte_count": byte_count, "used_before": used,
                "cap_bytes": tenant.artifact_cap_bytes,
            }),
            policy_revision=tenant.policy_revision, outcome="warning",
        ))
    return charge


def artifact_charge(
    db: Session, run: Run, kind: str, logical_key: str,
    sha256: str, byte_count: int,
) -> ArtifactCharge:
    charge = db.scalar(select(ArtifactCharge).where(
        ArtifactCharge.tenant_id == run.tenant_id,
        ArtifactCharge.run_id == run.id,
        ArtifactCharge.kind == kind,
        ArtifactCharge.logical_key == logical_key,
    ).with_for_update())
    if (
        charge is None or charge.sha256 != sha256
        or charge.byte_count != byte_count or charge.status == "deleted"
    ):
        raise ServiceError("ARTIFACT_QUOTA_MISSING", "Artifact reservation changed", 409)
    return charge


def activate_artifact(db: Session, charge: ArtifactCharge) -> None:
    if charge.status == "active":
        return
    if charge.status != "reserved":
        raise ServiceError("ARTIFACT_CONFLICT", "Artifact status changed", 409)
    charge.status = "active"
    charge.changed_at = utcnow()


def delete_artifact_charge(db: Session, run: Run, kind: str, logical_key: str) -> None:
    charge = db.scalar(select(ArtifactCharge).where(
        ArtifactCharge.tenant_id == run.tenant_id,
        ArtifactCharge.run_id == run.id,
        ArtifactCharge.kind == kind,
        ArtifactCharge.logical_key == logical_key,
    ).with_for_update())
    if charge is None or charge.status == "deleted":
        return
    charge.status = "deleted"
    charge.changed_at = utcnow()
