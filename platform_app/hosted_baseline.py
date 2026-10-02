"""Fenced hosted baseline handoff across Git, S3, EC2 and PostgreSQL."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.environment_manifest import EnvironmentManifest
from platform_app.models import Run, SandboxLease
from platform_app.repository_archive import SourceArchive
from platform_app.run_ledger import assert_fence, aware
from platform_app.sandbox_broker import (
    SandboxSpec,
    assert_sandbox_callback,
    attach_guest_bootstrap,
    launch_reserved_sandbox,
    reserve_sandbox,
    seal_bootstrapping_sandbox,
)
from platform_app.sandbox_bundle import build_guest_bundle
from platform_app.sandbox_guest_bootstrap import render_user_data
from platform_app.sandbox_transport import (
    GuestOutput,
    SandboxObjectKeys,
    fetch_guest_output,
    issue_guest_urls,
    stage_source_archive,
)
from platform_app.service import ServiceError, append_event


def stage_and_launch_baseline(
    db: Session, run_id: str, worker_id: str, fence: int,
    source: SourceArchive, spec: SandboxSpec, ec2, s3,
    bucket: str, envelope_key: bytes,
    *, phase: str = "baseline",
) -> SandboxLease:
    """Replay preserves a committed user-data envelope and EC2 client token."""
    run = db.get(Run, run_id)
    if run is None:
        raise ServiceError("NOT_FOUND", "Run not found", 404)
    assert_fence(run, worker_id, fence)
    if source.commit != run.base_commit:
        raise ServiceError("SOURCE_REVISION_MISMATCH", "Archive differs from run commit", 409)
    try:
        manifest = EnvironmentManifest.model_validate(
            run.config_snapshot["environment_manifest"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ServiceError(
            "ENVIRONMENT_UNAVAILABLE", "Run has no approved environment plan", 409
        ) from error
    bundle = build_guest_bundle(source, manifest)
    lease = reserve_sandbox(db, run.id, worker_id, fence, spec, phase=phase)
    db.commit()  # Existing-intent replay also releases its row lock before S3 I/O.
    keys = SandboxObjectKeys.scoped(
        lease.tenant_id, lease.project_id, lease.run_id, lease.id
    )
    staged_sha = stage_source_archive(s3, bucket, keys, bundle.archive)
    if staged_sha != bundle.sha256 or (
        lease.source_sha256 is not None and lease.source_sha256 != staged_sha
    ):
        raise ServiceError("SANDBOX_SOURCE_CONFLICT", "Staged source differs", 409)
    if lease.bootstrap_envelope is None:
        remaining = int((aware(lease.expires_at) - utcnow()).total_seconds())
        if remaining < 60:
            raise ServiceError("SANDBOX_REVOKED", "Sandbox lease has expired", 409)
        urls = issue_guest_urls(s3, bucket, keys, ttl_seconds=min(remaining, 1800))
        user_data = render_user_data(
            urls, lease.id, fence, bundle.sha256, bundle.manifest_sha256,
            int(aware(lease.expires_at).timestamp()), phase=phase,
        )
        attach_guest_bootstrap(
            db, lease.id, worker_id, fence, user_data, staged_sha, envelope_key
        )
    return launch_reserved_sandbox(db, lease.id, worker_id, fence, ec2, envelope_key)


def seal_and_collect_baseline(
    db: Session, lease_id: str, worker_id: str, fence: int,
    ec2, s3, bucket: str,
) -> GuestOutput | None:
    """A guest result is evidence only; it never sets the repair verdict."""
    lease = db.get(SandboxLease, lease_id)
    if lease is None:
        raise ServiceError("NOT_FOUND", "Sandbox lease not found", 404)
    if lease.state == "bootstrapping" and not seal_bootstrapping_sandbox(
        db, lease_id, worker_id, fence, ec2, s3, bucket
    ):
        return None
    if not lease.instance_id:
        raise ServiceError("SANDBOX_NOT_READY", "Sandbox has no instance", 409)
    assert_sandbox_callback(db, lease_id, lease.instance_id, fence)
    if not lease.source_sha256:
        raise ServiceError("SANDBOX_SOURCE_CONFLICT", "Sandbox source is missing", 409)
    keys = SandboxObjectKeys.scoped(
        lease.tenant_id, lease.project_id, lease.run_id, lease.id
    )
    output = fetch_guest_output(
        s3, bucket, keys, lease.id, fence, lease.source_sha256,
        phase=lease.phase,
    )
    if output is None:
        db.rollback()
        return None
    digest = hashlib.sha256(json.dumps(
        output.result, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    run = db.scalar(
        select(Run).where(Run.id == lease.run_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    lease = db.scalar(
        select(SandboxLease).where(SandboxLease.id == lease_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    assert_fence(run, worker_id, fence)
    if lease.result_sha256 is not None:
        if lease.result_sha256 != digest:
            raise ServiceError("SANDBOX_RESULT_CONFLICT", "Guest result changed", 409)
        db.rollback()
        return output
    assert_sandbox_callback(db, lease_id, lease.instance_id, fence)
    lease.result_sha256 = digest
    lease.result_summary = output.result
    lease.result_received_at = utcnow()
    append_event(db, run, f"sandbox.{lease.phase}_recorded", {
        "sandbox_lease_id": lease.id,
        "result_sha256": digest,
        "evidence_sha256": output.result["evidence_sha256"],
        "guest_exit_code": output.result["guest_exit_code"],
        "observation_status": (output.result.get(lease.phase) or {}).get("status"),
    })
    db.commit()
    return output
