"""Tenant media-minute liability for each hosted encode attempt."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import AuditEvent, MediaMinuteCharge, Run
from platform_app.service import ServiceError, canonical_hash
from platform_app.tenant_quota import QuotaError, lock_tenant


def media_usage_seconds(db: Session, tenant_id: str, *, now: datetime) -> int:
    day = datetime(now.year, now.month, now.day, tzinfo=UTC)
    charges = db.scalars(select(MediaMinuteCharge.reserved_seconds).where(
        MediaMinuteCharge.tenant_id == tenant_id,
        MediaMinuteCharge.created_at >= day,
        MediaMinuteCharge.created_at < day + timedelta(days=1),
    )).all()
    return sum(charges)


def reserve_media_attempt(
    db: Session, run_id: str, label: str, attempt: int,
    source_sha256: str, duration_seconds: float,
) -> MediaMinuteCharge:
    if (
        label not in {"baseline", "candidate"} or attempt < 1
        or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        or not math.isfinite(duration_seconds)
        or not 0 < duration_seconds <= 900
    ):
        raise ServiceError("MEDIA_SOURCE_INVALID", "Media reservation is invalid", 409)
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise ServiceError("MEDIA_SOURCE_INVALID", "Media run is missing", 409)
    try:
        tenant = lock_tenant(db, run.tenant_id)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 403) from error
    seconds = math.ceil(duration_seconds)
    prior = db.scalar(select(MediaMinuteCharge).where(
        MediaMinuteCharge.tenant_id == tenant.id,
        MediaMinuteCharge.run_id == run.id,
        MediaMinuteCharge.label == label,
        MediaMinuteCharge.attempt == attempt,
    ).with_for_update())
    if prior is not None:
        if prior.source_sha256 != source_sha256 or prior.reserved_seconds != seconds:
            raise ServiceError("MEDIA_SOURCE_CONFLICT", "Media attempt changed", 409)
        return prior
    now = utcnow()
    used = media_usage_seconds(db, tenant.id, now=now)
    cap = tenant.daily_media_minutes * 60
    if used + seconds > cap:
        raise ServiceError("MEDIA_QUOTA_EXHAUSTED", "Tenant daily media-minute cap reached", 409)
    charge = MediaMinuteCharge(
        tenant_id=tenant.id, run_id=run.id, label=label, attempt=attempt,
        source_sha256=source_sha256, reserved_seconds=seconds,
        status="reserved", created_at=now,
    )
    db.add(charge)
    db.flush()
    if used < cap * 0.8 <= used + seconds:
        db.add(AuditEvent(
            tenant_id=tenant.id, actor="media-worker", action="media.threshold_80",
            target_ref=run.id,
            arguments_hash=canonical_hash({
                "run_id": run.id, "label": label, "attempt": attempt,
                "reserved_seconds": seconds, "used_before": used,
                "daily_cap_seconds": cap,
            }),
            policy_revision=tenant.policy_revision, outcome="warning",
        ))
    return charge


def finish_media_attempt(db: Session, charge_id: str, *, succeeded: bool) -> None:
    charge = db.scalar(select(MediaMinuteCharge).where(
        MediaMinuteCharge.id == charge_id,
    ).with_for_update())
    if charge is None:
        raise ServiceError("MEDIA_QUOTA_MISSING", "Media reservation is missing", 409)
    if charge.status == "completed" and succeeded:
        return
    if charge.status == "failed" and not succeeded:
        return
    if charge.status != "reserved":
        raise ServiceError("MEDIA_QUOTA_CONFLICT", "Media attempt outcome changed", 409)
    charge.status = "completed" if succeeded else "failed"
    charge.completed_at = utcnow()
