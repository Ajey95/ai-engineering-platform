"""Daily tenant sandbox-minute liability from durable guest leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.models import SandboxLease


def sandbox_usage_seconds(
    db: Session, tenant_id: str, *, now: datetime,
) -> int:
    day = datetime(now.year, now.month, now.day, tzinfo=UTC)
    leases = db.scalars(select(SandboxLease).where(
        SandboxLease.tenant_id == tenant_id,
        SandboxLease.created_at >= day,
        SandboxLease.created_at < day + timedelta(days=1),
    )).all()
    return sum(
        lease.used_seconds if lease.state == "terminated" and lease.used_seconds is not None
        else lease.reserved_seconds
        for lease in leases
    )
