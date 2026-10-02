"""Publish run-dispatch wakeups from the transactional outbox to SQS."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import OutboxEvent, Run
from platform_app.service import ServiceError


def publish_run_dispatches(db: Session, sqs, queue_url: str, limit: int = 20) -> int:
    """SQS may receive a duplicate after a lost DB receipt; the run ledger fences it."""
    if not queue_url.startswith("https://") or not 1 <= limit <= 100:
        raise ValueError("An HTTPS queue URL and bounded batch are required")
    ids = list(db.scalars(
        select(OutboxEvent.id).where(
            OutboxEvent.topic == "run.dispatch",
            OutboxEvent.status == "pending",
            OutboxEvent.queue_published_at.is_(None),
        ).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(limit)
    ))
    sent = 0
    for event_id in ids:
        event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.topic == "run.dispatch",
                OutboxEvent.status == "pending",
                OutboxEvent.queue_published_at.is_(None),
            ).with_for_update(skip_locked=True).execution_options(populate_existing=True)
        )
        if event is None:
            continue
        run = db.scalar(select(Run).where(
            Run.id == event.payload.get("run_id"),
            Run.tenant_id == event.tenant_id,
        ))
        if run is None:
            event.status = "failed"
            db.commit()
            continue
        body = json.dumps({
            "version": 1, "event_id": event.id, "tenant_id": event.tenant_id,
            "run_id": run.id,
        }, sort_keys=True, separators=(",", ":"))
        try:
            response = sqs.send_message(QueueUrl=queue_url, MessageBody=body)
        except Exception:
            db.rollback()
            raise
        if not response.get("MessageId"):
            db.rollback()
            raise ServiceError("QUEUE_OUTCOME_UNKNOWN", "SQS send receipt is missing", 503)
        event.queue_published_at = utcnow()
        db.commit()
        sent += 1
    return sent


def reset_stale_queue_receipt(db: Session, event_id: str, *, operator: bool = False) -> None:
    """Operator recovery after confirming SQS/DLQ state; duplicate is safe."""
    if not operator:
        raise ServiceError("FORBIDDEN", "Operator recovery is required", 403)
    event = db.scalar(select(OutboxEvent).where(
        OutboxEvent.id == event_id, OutboxEvent.topic == "run.dispatch",
    ).with_for_update())
    if event is None:
        raise ServiceError("NOT_FOUND", "Dispatch event not found", 404)
    if event.status != "pending":
        raise ServiceError("DISPATCH_CLOSED", "Only a pending event can be republished", 409)
    event.queue_published_at = None
    db.commit()
