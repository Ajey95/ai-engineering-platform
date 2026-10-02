"""Serialized per-tenant byte cap for evidence bundle downloads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import AuditEvent, ExportCharge
from platform_app.service import canonical_hash
from platform_app.tenant_quota import QuotaError, lock_tenant


def reserve_export(
    db: Session, tenant_id: str, run_id: str, actor: str,
    bytes_count: int, archive_sha256: str, *, now: datetime | None = None,
) -> ExportCharge:
    if bytes_count <= 0 or len(archive_sha256) != 64 or not actor.strip():
        raise ValueError("Export receipt is invalid")
    now = now or utcnow()
    tenant = lock_tenant(db, tenant_id)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    used = db.scalar(select(func.coalesce(func.sum(ExportCharge.bytes_count), 0)).where(
        ExportCharge.tenant_id == tenant_id,
        ExportCharge.created_at >= start,
        ExportCharge.created_at < end,
    )) or 0
    cap = tenant.daily_export_cap_bytes
    if used + bytes_count > cap:
        raise QuotaError("EXPORT_QUOTA_EXHAUSTED", "Tenant daily export cap reached")
    charge = ExportCharge(
        tenant_id=tenant_id, run_id=run_id, actor=actor,
        bytes_count=bytes_count, archive_sha256=archive_sha256, created_at=now,
    )
    db.add(charge)
    if used < cap * 0.8 <= used + bytes_count:
        db.add(AuditEvent(
            tenant_id=tenant_id, actor=actor, action="export.threshold_80",
            target_ref=run_id,
            arguments_hash=canonical_hash({
                "run_id": run_id, "bytes_count": bytes_count,
                "used_before": used, "daily_cap": cap,
            }),
            policy_revision=tenant.policy_revision, outcome="warning",
        ))
    return charge
