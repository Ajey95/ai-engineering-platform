"""Tenant-scoped operational snapshot from persisted control-plane records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.alert_delivery import validate_pager_destination
from platform_app.artifact_quota import artifact_usage_bytes
from platform_app.config import settings
from platform_app.db import utcnow
from platform_app.media_quota import media_usage_seconds
from platform_app.model_qualification import qualification_current
from platform_app.models import (
    BudgetEntry,
    ExportCharge,
    ModelEntry,
    OperationalAlert,
    OperationalAlertEvent,
    OutboxEvent,
    Run,
    RunEvent,
    SandboxLease,
    Tenant,
    ToolAction,
)
from platform_app.operational_alerts import SANDBOX_CLEANUP_GRACE, alert_read
from platform_app.ops_alerts import current_warning_details
from platform_app.sandbox_quota import sandbox_usage_seconds


def _age(now: datetime, created_at: datetime | None) -> int | None:
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return max(0, int((now - created_at).total_seconds()))


def _model_call_metrics(db: Session, tenant_id: str, cutoff: datetime) -> dict:
    rows = db.scalars(select(RunEvent).where(
        RunEvent.tenant_id == tenant_id,
        RunEvent.created_at >= cutoff,
        RunEvent.event_type.in_([
            "model.started", "model.completed", "model.rejected", "model.uncertain",
            "context.compacted",
        ]),
    ).order_by(RunEvent.run_id, RunEvent.sequence).limit(10_001)).all()
    if len(rows) > 10_000:
        return {"status": "TRUNCATED", "completed_count": None,
                "definite_rejection_count": None, "uncertain_count": None,
                "unsettled_count": None,
                "completed_latency_ms_p50": None, "completed_latency_ms_p95": None,
                "definite_rejection_rate": None, "context_compaction_count": None,
                "estimated_input_tokens_p50": None,
                "input_estimation_error_pct_p50": None,
                "input_estimation_samples": None}
    starts: dict[tuple[str, str], tuple[datetime, int | None]] = {}
    uncertain_steps: set[tuple[str, str]] = set()
    completed = rejected = compactions = 0
    latencies: list[int] = []
    estimates: list[int] = []
    errors: list[float] = []
    for event in rows:
        if event.event_type == "context.compacted":
            compactions += 1
            continue
        step = event.payload.get("step_id") if isinstance(event.payload, dict) else None
        if not isinstance(step, str) or not step:
            continue
        key = event.run_id, step
        instant = event.created_at
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        if event.event_type == "model.started":
            estimate = event.payload.get("estimated_input_tokens")
            if not isinstance(estimate, int) or isinstance(estimate, bool) or estimate < 0:
                estimate = None
            starts[key] = instant, estimate
            if estimate is not None:
                estimates.append(estimate)
        elif event.event_type == "model.completed" and key in starts:
            completed += 1
            began, estimate = starts.pop(key)
            latencies.append(max(0, int((instant - began).total_seconds() * 1000)))
            actual = event.payload.get("input_tokens")
            if (estimate is not None and isinstance(actual, int)
                    and not isinstance(actual, bool) and actual > 0):
                errors.append(abs(estimate - actual) * 100 / actual)
            uncertain_steps.discard(key)
        elif event.event_type == "model.rejected" and key in starts:
            rejected += 1
            starts.pop(key)
            uncertain_steps.discard(key)
        elif event.event_type == "model.uncertain" and key in starts:
            uncertain_steps.add(key)
    latencies.sort()
    estimates.sort()
    errors.sort()

    def percentile(values: list, fraction: float):
        return values[max(0, ceil(len(values) * fraction) - 1)] if values else None

    decided = completed + rejected
    return {
        "status": "MEASURED", "completed_count": completed,
        "definite_rejection_count": rejected, "unsettled_count": len(starts),
        "uncertain_count": len(uncertain_steps),
        "completed_latency_ms_p50": percentile(latencies, 0.5),
        "completed_latency_ms_p95": percentile(latencies, 0.95),
        "context_compaction_count": compactions,
        "estimated_input_tokens_p50": percentile(estimates, 0.5),
        "input_estimation_error_pct_p50": (
            round(percentile(errors, 0.5), 2) if errors else None
        ),
        "input_estimation_samples": len(errors),
        "definite_rejection_rate": round(rejected / decided, 4) if decided else None,
    }


def _component_status(
    db: Session, config, *, queue_age: int | None, graph_age: int | None,
    media_age: int | None, expired_leases: int,
) -> list[dict[str, str]]:
    """Report only what this request can prove from configuration and canonical rows."""
    qualified = sum(
        1 for model in db.scalars(select(ModelEntry)).all()
        if qualification_current(model)
        and not (model.capabilities or {}).get("database_fixture_only")
    )
    queue_status = "DEGRADED" if queue_age is not None and queue_age > 300 else "UNVERIFIED"
    graph_status = (
        "DISABLED" if not config.memgraph_uri else
        "DEGRADED" if graph_age is not None and graph_age > 60 else "UNVERIFIED"
    )
    media_status = (
        "DEGRADED" if media_age is not None and media_age > 600 else
        "UNVERIFIED" if config.private_media_bucket else "LOCAL_ONLY"
    )
    sandbox_status = (
        "DISABLED" if not config.hosted_execution_enabled else
        "DEGRADED" if expired_leases else "UNVERIFIED"
    )
    return [
        {"id": "control_api", "status": "SERVING",
         "detail": "This authenticated API request completed."},
        {"id": "canonical_database", "status": "AVAILABLE",
         "detail": "Canonical database queries completed for this snapshot."},
        {"id": "run_dispatch", "status": queue_status,
         "detail": "Queued run age exceeds five minutes." if queue_status == "DEGRADED"
         else "Worker liveness is not proven by the queue snapshot."},
        {"id": "graph_projection", "status": graph_status,
         "detail": "Graph projection is not configured." if graph_status == "DISABLED"
         else "Graph projection backlog exceeds one minute." if graph_status == "DEGRADED"
         else "Graph service reachability is not probed by this snapshot."},
        {"id": "model_providers", "status": "UNVERIFIED" if qualified else "UNAVAILABLE",
         "detail": (
             f"{qualified} non-fixture model entries have current qualification; "
             "live provider reachability is not probed."
         )
         if qualified else "No non-fixture model entry has current qualification."},
        {"id": "sandbox_execution", "status": sandbox_status,
         "detail": "Hosted sandbox execution is disabled." if sandbox_status == "DISABLED"
         else "Expired sandbox leases require cleanup." if sandbox_status == "DEGRADED"
         else "Hosted VM launch and isolation are not probed by this snapshot."},
        {"id": "media_processing", "status": media_status,
         "detail": "Media job age exceeds ten minutes." if media_status == "DEGRADED"
         else "Only local media storage is configured." if media_status == "LOCAL_ONLY"
         else "Remote media processing and playback are not probed by this snapshot."},
    ]


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
    media_pending, oldest_media = db.execute(select(
        func.count(OutboxEvent.id), func.min(OutboxEvent.created_at),
    ).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == "media.transcode",
        OutboxEvent.status.in_(["pending", "processing"]),
    )).one()
    expired_leases = db.scalar(select(func.count(SandboxLease.id)).where(
        SandboxLease.tenant_id == tenant_id,
        SandboxLease.state != "terminated",
        SandboxLease.expires_at <= now - SANDBOX_CLEANUP_GRACE,
    )) or 0
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
    media_age = _age(now, oldest_media)
    warnings = []
    if queue_age is not None and queue_age > 300:
        warnings.append("runnable_queue_over_5_minutes")
    if graph_age is not None and graph_age > 60:
        warnings.append("graph_projection_over_60_seconds")
    if media_age is not None and media_age > 600:
        warnings.append("media_encode_age")
    if expired_leases:
        warnings.append("sandbox_orphan")
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
        "components": _component_status(
            db, config, queue_age=queue_age, graph_age=graph_age,
            media_age=media_age, expired_leases=expired_leases,
        ),
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
        "media_queue": {"pending_count": media_pending, "oldest_age_seconds": media_age},
        "sandbox": {"expired_lease_count": expired_leases,
                    "cleanup_grace_seconds": int(SANDBOX_CLEANUP_GRACE.total_seconds()),
                    "used_minutes_today": round(
                        sandbox_usage_seconds(db, tenant_id, now=now) / 60, 2
                    ),
                    "daily_cap_minutes": tenant.daily_sandbox_minutes},
        "tools": {"by_policy_result": tools, "failed_count": failed_tools},
        "media": {
            "by_status": media,
            "used_minutes_today": round(
                media_usage_seconds(db, tenant_id, now=now) / 60, 2
            ),
            "daily_cap_minutes": tenant.daily_media_minutes,
        },
        "model_calls": _model_call_metrics(db, tenant_id, cutoff),
        "inference_budget": {
            "reserved_usd": str(Decimal(reserved)), "actual_usd": str(Decimal(actual)),
        },
        "exports": {
            "used_bytes_today": export_bytes,
            "daily_cap_bytes": tenant.daily_export_cap_bytes,
        },
        "artifacts": {
            "used_bytes": artifact_usage_bytes(db, tenant_id),
            "cap_bytes": tenant.artifact_cap_bytes,
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
            "provider_time_to_first_event",
            "sandbox_utilization", "abr_playback_quality",
        ],
    }
