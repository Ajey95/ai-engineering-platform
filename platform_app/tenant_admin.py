"""Tenant owner quota controls with a durable change audit."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import AuditEvent, Tenant
from platform_app.schemas import TenantQuotasSet
from platform_app.service import ServiceError, canonical_hash


def quota_read(tenant: Tenant) -> dict:
    return {
        "daily_inference_cap_usd": format(tenant.daily_inference_cap_usd, ".6f"),
        "monthly_inference_cap_usd": format(tenant.monthly_inference_cap_usd, ".6f"),
        "max_concurrent_runs": tenant.max_concurrent_runs,
        "daily_export_cap_bytes": tenant.daily_export_cap_bytes,
        "daily_sandbox_minutes": tenant.daily_sandbox_minutes,
        "daily_media_minutes": tenant.daily_media_minutes,
        "artifact_cap_bytes": tenant.artifact_cap_bytes,
    }


def update_quotas(
    db: Session, tenant_id: str, actor: str, requested: TenantQuotasSet,
) -> dict:
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "active":
        raise ServiceError("TENANT_DISABLED", "Tenant is not active", 403)
    before = quota_read(tenant)
    after = {
        "daily_inference_cap_usd": format(requested.daily_inference_cap_usd, ".6f"),
        "monthly_inference_cap_usd": format(requested.monthly_inference_cap_usd, ".6f"),
        "max_concurrent_runs": requested.max_concurrent_runs,
        "daily_export_cap_bytes": requested.daily_export_cap_bytes,
        "daily_sandbox_minutes": (
            requested.daily_sandbox_minutes
            if requested.daily_sandbox_minutes is not None else tenant.daily_sandbox_minutes
        ),
        "daily_media_minutes": (
            requested.daily_media_minutes
            if requested.daily_media_minutes is not None else tenant.daily_media_minutes
        ),
        "artifact_cap_bytes": (
            requested.artifact_cap_bytes
            if requested.artifact_cap_bytes is not None else tenant.artifact_cap_bytes
        ),
    }
    if before != after:
        tenant.daily_inference_cap_usd = requested.daily_inference_cap_usd
        tenant.monthly_inference_cap_usd = requested.monthly_inference_cap_usd
        tenant.max_concurrent_runs = requested.max_concurrent_runs
        tenant.daily_export_cap_bytes = requested.daily_export_cap_bytes
        tenant.daily_sandbox_minutes = after["daily_sandbox_minutes"]
        tenant.daily_media_minutes = after["daily_media_minutes"]
        tenant.artifact_cap_bytes = after["artifact_cap_bytes"]
        db.add(AuditEvent(
            tenant_id=tenant_id, actor=actor, action="tenant.quotas.update",
            target_ref=tenant_id,
            arguments_hash=canonical_hash({
                "before": before, "after": after, "reason": requested.reason.strip(),
            }),
            policy_revision=tenant.policy_revision,
            outcome="allowed",
        ))
    return after
