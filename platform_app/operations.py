"""Tenant-scoped operational snapshot from persisted control-plane records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.alert_delivery import validate_pager_destination
from platform_app.config import settings
from platform_app.db import utcnow
from platform_app.models import (
    BudgetEntry,
    ExportCharge,
    OperationalAlert,
    OperationalAlertEvent,
    OutboxEvent,
    Run,
    RunEvent,
    Tenant,
    ToolAction,
)
from platform_app.operational_alerts import alert_read
from platform_app.ops_alerts import current_warning_details


def _age(now: datetime, created_at: datetime | None) -> int | None:
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return max(0, int((now - created_at).total_seconds()))


def operations_snapshot(
    db: Session, tenant_id: str, *, now: datetime | None = None,
) -> dict:
    now = now or utcnow()
    cutoff = now - timedelta(hours=24)
    states = dict(db.execute(select(Run.state, func.count(Run.id)).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
    ).group_by(Run.state)).all())
    verdicts = dict(db.execute(select(Run.verdict, func.count(Run.id)).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
        Run.state.in_(["COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"]),
    ).group_by(Run.verdict)).all())
    closed = sum(verdicts.values())
    passed = db.scalar(select(func.count(Run.id)).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
        Run.state == "COMPLETED", Run.verdict == "PASSED",
    )) or 0
    inconclusive = db.scalar(select(func.count(Run.id)).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
        Run.state == "INCONCLUSIVE",
    )) or 0
    reviewed = db.scalar(select(func.count(RunEvent.id)).join(
        Run, (RunEvent.run_id == Run.id) & (RunEvent.tenant_id == Run.tenant_id)
    ).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
        RunEvent.event_type == "review.decision",
    )) or 0
    accepted = db.scalar(select(func.count(RunEvent.id)).join(
        Run, (RunEvent.run_id == Run.id) & (RunEvent.tenant_id == Run.tenant_id)
    ).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
        RunEvent.event_type == "review.decision",
        RunEvent.payload["decision"].as_string() == "accepted",
    )) or 0
    queued, oldest_queued = db.execute(select(
        func.count(Run.id), func.min(Run.created_at),
    ).where(Run.tenant_id == tenant_id, Run.state == "QUEUED")).one()
    graph_pending, oldest_graph = db.execute(select(
        func.count(OutboxEvent.id), func.min(OutboxEvent.created_at),
    ).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == "memory.project",
        OutboxEvent.status == "pending",
    )).one()
    tools = dict(db.execute(select(ToolAction.policy_result, func.count(ToolAction.id)).where(
        ToolAction.tenant_id == tenant_id, ToolAction.created_at >= cutoff,
    ).group_by(ToolAction.policy_result)).all())
    failed_tools = db.scalar(select(func.count(ToolAction.id)).where(
        ToolAction.tenant_id == tenant_id, ToolAction.created_at >= cutoff,
        ToolAction.status == "FAILED",
    )) or 0
    media = dict(db.execute(select(Run.media_status, func.count(Run.id)).where(
        Run.tenant_id == tenant_id, Run.created_at >= cutoff,
    ).group_by(Run.media_status)).all())
    call_budget = (
        BudgetEntry.tenant_id == tenant_id,
        BudgetEntry.created_at >= cutoff,
        BudgetEntry.category.like("call:%"),
    )
    reserved = db.scalar(select(func.coalesce(func.sum(BudgetEntry.reserved_usd), 0)).where(
        *call_budget, BudgetEntry.status == "reserved",
    ))
    actual = db.scalar(select(func.coalesce(func.sum(BudgetEntry.actual_usd), 0)).where(
        *call_budget,
    ))
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    export_bytes = db.scalar(select(func.coalesce(func.sum(ExportCharge.bytes_count), 0)).where(
        ExportCharge.tenant_id == tenant_id,
        ExportCharge.created_at >= day_start,
        ExportCharge.created_at < day_start + timedelta(days=1),
    )) or 0
    tenant = db.get(Tenant, tenant_id)
    queue_age = _age(now, oldest_queued)
    graph_age = _age(now, oldest_graph)
    warnings = []
    if queue_age is not None and queue_age > 300:
        warnings.append("runnable_queue_over_5_minutes")
    if graph_age is not None and graph_age > 60:
        warnings.append("graph_projection_over_60_seconds")
    active_alerts = db.scalars(select(OperationalAlert).where(
        OperationalAlert.tenant_id == tenant_id,
        OperationalAlert.state.in_(["observing", "firing"]),
    ).order_by(OperationalAlert.first_seen_at)).all()
    pending_notifications, oldest_notification = db.execute(select(
        func.count(OperationalAlertEvent.id), func.min(OperationalAlertEvent.created_at),
    ).where(
        OperationalAlertEvent.tenant_id == tenant_id,
        OperationalAlertEvent.notification_status == "pending",
    )).one()
    delivered_notifications = db.scalar(select(func.count(OperationalAlertEvent.id)).where(
        OperationalAlertEvent.tenant_id == tenant_id,
        OperationalAlertEvent.notification_status == "delivered",
        OperationalAlertEvent.created_at >= cutoff,
    )) or 0
    config = settings()
    try:
        validate_pager_destination(config.pager_webhook_url, config.pager_webhook_secret)
        pager_configured = True
    except ValueError:
        pager_configured = False
    return {
        "window_start": cutoff.isoformat(), "observed_at": now.isoformat(),
        "runs": {
            "by_state": states, "closed_by_verdict": verdicts,
            "closed_count": closed, "reviewed_count": reviewed,
            "verification_pass_rate": round(passed / closed, 4) if closed else None,
            "inconclusive_rate": round(inconclusive / closed, 4)
            if closed else None,
            "review_acceptance_rate": round(accepted / reviewed, 4) if reviewed else None,
        },
        "queue": {"queued_count": queued, "oldest_age_seconds": queue_age},
        "graph": {"pending_count": graph_pending, "oldest_age_seconds": graph_age},
        "tools": {"by_policy_result": tools, "failed_count": failed_tools},
        "media": {"by_status": media},
        "inference_budget": {
            "reserved_usd": str(Decimal(reserved)), "actual_usd": str(Decimal(actual)),
        },
        "exports": {
            "used_bytes_today": export_bytes,
            "daily_cap_bytes": tenant.daily_export_cap_bytes,
        },
        "warnings_now": warnings,
        "warning_details": current_warning_details(warnings),
        "active_alerts": [alert_read(row) for row in active_alerts],
        "pager_delivery": {
            "configured": pager_configured,
            "pending_count": pending_notifications,
            "oldest_pending_age_seconds": _age(now, oldest_notification),
            "delivered_count_24h": delivered_notifications,
        },
        "unavailable": [
            "provider_latency_and_error_rate", "context_size_and_compaction_rate",
            "token_estimation_error", "sandbox_utilization", "abr_playback_quality",
        ],
    }
