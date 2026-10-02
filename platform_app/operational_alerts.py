"""Persist evaluated alert state without inferring continuity across monitor outages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import (
    OperationalAlert,
    OperationalAlertEvent,
    OutboxEvent,
    Run,
    RunEvent,
    Tenant,
)
from platform_app.ops_alerts import ALERTS
from platform_app.service import ServiceError

MAX_SAMPLE_GAP = timedelta(seconds=90)
QUEUE_SUSTAINED = timedelta(minutes=10)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _transition(
    db: Session, alert: OperationalAlert, state: str, actor: str, reason: str,
    now: datetime,
) -> None:
    alert.state = state
    if state == "firing":
        alert.fired_at = now
        alert.resolved_at = None
    elif state == "resolved":
        alert.resolved_at = now
    db.add(OperationalAlertEvent(
        alert_id=alert.id, tenant_id=alert.tenant_id, generation=alert.generation,
        state=state, actor=actor, reason=reason, evidence=alert.evidence,
        created_at=now,
        notification_status="pending" if state in {"firing", "resolved"} else "not_required",
        notification_next_attempt_at=now if state in {"firing", "resolved"} else None,
    ))


def _evaluate_threshold(
    db: Session, *, tenant_id: str, alert_id: str, exceeded: bool,
    evidence: dict, now: datetime, sustained_for: timedelta,
    current: dict[str, OperationalAlert],
) -> None:
    alert = current.get(alert_id)
    if not exceeded:
        if alert is not None and alert.state in {"observing", "firing"}:
            alert.last_observed_at = now
            alert.evidence = evidence
            _transition(db, alert, "resolved", "operations-monitor", "Threshold cleared", now)
        return
    if alert is None:
        alert = OperationalAlert(
            tenant_id=tenant_id, alert_id=alert_id, state="observing",
            generation=1, first_seen_at=now, last_observed_at=now,
            evidence=evidence,
        )
        db.add(alert)
        db.flush()
        current[alert_id] = alert
        _transition(db, alert, "observing", "operations-monitor", "Threshold first observed", now)
    elif alert.state == "resolved":
        alert.generation += 1
        alert.first_seen_at = now
        alert.last_observed_at = now
        alert.fired_at = None
        alert.evidence = evidence
        _transition(db, alert, "observing", "operations-monitor", "Threshold observed again", now)
    else:
        if now - _aware(alert.last_observed_at) > MAX_SAMPLE_GAP:
            # A missed interval cannot establish a sustained condition.
            alert.first_seen_at = now
        alert.last_observed_at = now
        alert.evidence = evidence
    if alert.state == "observing" and now - _aware(alert.first_seen_at) >= sustained_for:
        _transition(db, alert, "firing", "operations-monitor", "Sustained threshold met", now)


def evaluate_tenant_alerts(
    db: Session, tenant_id: str, *, now: datetime | None = None,
) -> list[OperationalAlert]:
    """Evaluate one tenant under a row lock; caller commits the transaction."""
    now = now or utcnow()
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None:
        raise ServiceError("TENANT_NOT_FOUND", "Tenant does not exist", 404)
    current = {
        row.alert_id: row for row in db.scalars(select(OperationalAlert).where(
            OperationalAlert.tenant_id == tenant_id,
        ).with_for_update()).all()
    }
    queued, oldest_queued = db.execute(select(
        func.count(Run.id), func.min(Run.created_at),
    ).where(Run.tenant_id == tenant_id, Run.state == "QUEUED")).one()
    pending, oldest_graph = db.execute(select(
        func.count(OutboxEvent.id), func.min(OutboxEvent.created_at),
    ).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == "memory.project", OutboxEvent.status == "pending",
    )).one()
    queue_age = max(0, int((now - _aware(oldest_queued)).total_seconds())) if oldest_queued else 0
    graph_age = max(0, int((now - _aware(oldest_graph)).total_seconds())) if oldest_graph else 0
    _evaluate_threshold(
        db, tenant_id=tenant_id, alert_id="runnable_queue_over_5_minutes",
        exceeded=bool(queued and queue_age > 300),
        evidence={"queued_count": queued, "oldest_age_seconds": queue_age},
        now=now, sustained_for=QUEUE_SUSTAINED, current=current,
    )
    _evaluate_threshold(
        db, tenant_id=tenant_id, alert_id="graph_projection_over_60_seconds",
        exceeded=bool(pending and graph_age > 60),
        evidence={"pending_count": pending, "oldest_age_seconds": graph_age},
        now=now, sustained_for=timedelta(), current=current,
    )
    newest_breach = db.scalar(select(RunEvent).join(
        Run, (RunEvent.run_id == Run.id) & (RunEvent.tenant_id == Run.tenant_id)
    ).where(
        Run.tenant_id == tenant_id, RunEvent.event_type == "budget.breached",
        RunEvent.created_at >= now - timedelta(hours=24),
    ).order_by(RunEvent.created_at.desc(), RunEvent.id.desc()).limit(1))
    alert = current.get("budget_breach")
    if newest_breach is not None and (alert is None or alert.source_cursor != newest_breach.id):
        evidence = {"run_id": newest_breach.run_id, "event_id": newest_breach.id}
        if alert is None:
            alert = OperationalAlert(
                tenant_id=tenant_id, alert_id="budget_breach", state="observing",
                generation=1, first_seen_at=now, last_observed_at=now,
                source_cursor=newest_breach.id, evidence=evidence,
            )
            db.add(alert)
            db.flush()
            current["budget_breach"] = alert
        else:
            if alert.state == "resolved":
                alert.generation += 1
                alert.first_seen_at = now
            alert.source_cursor = newest_breach.id
            alert.last_observed_at = now
            alert.evidence = evidence
        _transition(db, alert, "firing", "operations-monitor", "Budget breach event persisted", now)
    return list(current.values())


def resolve_alert(
    db: Session, tenant_id: str, alert_id: str, actor: str, reason: str,
    *, now: datetime | None = None,
) -> OperationalAlert:
    if len(reason.strip()) < 8:
        raise ServiceError("ALERT_REASON_REQUIRED", "Resolution needs a reason", 400)
    alert = db.scalar(select(OperationalAlert).where(
        OperationalAlert.tenant_id == tenant_id, OperationalAlert.id == alert_id,
    ).with_for_update())
    if alert is None:
        raise ServiceError("ALERT_NOT_FOUND", "Alert does not exist", 404)
    if alert.state != "firing":
        raise ServiceError("ALERT_STATE", "Only firing alerts can be resolved", 409)
    now = now or utcnow()
    alert.last_observed_at = now
    _transition(db, alert, "resolved", actor, reason.strip(), now)
    return alert


def alert_read(alert: OperationalAlert) -> dict:
    spec = ALERTS[alert.alert_id]
    return {
        "id": alert.id, "alert_id": alert.alert_id, "state": alert.state,
        "generation": alert.generation, "severity": spec.severity,
        "owner": spec.owner, "impact": spec.impact, "runbook": spec.runbook,
        "first_seen_at": alert.first_seen_at.isoformat(),
        "last_observed_at": alert.last_observed_at.isoformat(),
        "fired_at": alert.fired_at.isoformat() if alert.fired_at else None,
        "evidence": alert.evidence,
    }
