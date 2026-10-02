"""Read exact guest evidence only after a scoped database receipt exists."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import Run, SandboxLease
from platform_app.sandbox_transport import (
    SandboxObjectKeys,
    SandboxTransportError,
    fetch_guest_output,
)
from platform_app.service import ServiceError


def verified_guest_evidence(
    db: Session, run: Run, phase: str, s3, bucket: str,
) -> bytes:
    if phase not in {"baseline", "candidate"}:
        raise ServiceError("EVIDENCE_UNAVAILABLE", "Unknown guest phase", 404)
    lease = db.scalar(select(SandboxLease).where(
        SandboxLease.tenant_id == run.tenant_id,
        SandboxLease.project_id == run.project_id,
        SandboxLease.run_id == run.id,
        SandboxLease.phase == phase,
        SandboxLease.result_sha256.is_not(None),
    ).order_by(SandboxLease.generation.desc()).limit(1))
    if lease is None or not lease.source_sha256 or lease.result_received_at is None:
        raise ServiceError("EVIDENCE_UNAVAILABLE", "Guest evidence is not recorded", 404)
    keys = SandboxObjectKeys.scoped(
        lease.tenant_id, lease.project_id, lease.run_id, lease.id
    )
    try:
        output = fetch_guest_output(
            s3, bucket, keys, lease.id, lease.lease_fence, lease.source_sha256,
            phase=phase,
        )
    except SandboxTransportError as error:
        raise ServiceError("EVIDENCE_CONFLICT", "Guest evidence is invalid", 409) from error
    if output is None:
        raise ServiceError("EVIDENCE_UNAVAILABLE", "Guest evidence is unavailable", 404)
    digest = hashlib.sha256(json.dumps(
        output.result, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    if digest != lease.result_sha256 or output.result != lease.result_summary:
        raise ServiceError("EVIDENCE_CONFLICT", "Guest evidence changed", 409)
    return output.evidence_archive
