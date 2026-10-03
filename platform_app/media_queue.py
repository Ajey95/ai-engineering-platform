"""SQS wakeups for the canonical hosted media outbox."""

from __future__ import annotations

import json

from sqlalchemy import select

from platform_app.db import utcnow
from platform_app.models import OutboxEvent, Run
from platform_app.queue_consumer import _valid_message, _VisibilityHeartbeat
from platform_app.run_ledger import aware
from platform_app.service import ServiceError
from platform_app.telemetry import extract_trace, set_safe_attributes, tracer


def publish_media_dispatches(db, sqs, queue_url: str, limit: int = 20) -> int:
    if not queue_url.startswith("https://") or not 1 <= limit <= 100:
        raise ValueError("A bounded HTTPS media queue is required")
    ids = list(db.scalars(select(OutboxEvent.id).where(
        OutboxEvent.topic == "media.transcode",
        OutboxEvent.status == "pending",
        OutboxEvent.queue_published_at.is_(None),
    ).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(limit)))
    sent = 0
    for event_id in ids:
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.id == event_id,
            OutboxEvent.topic == "media.transcode",
            OutboxEvent.status == "pending",
            OutboxEvent.queue_published_at.is_(None),
        ).with_for_update(skip_locked=True))
        if event is None:
            continue
        run = db.scalar(select(Run).where(
            Run.id == event.payload.get("run_id"),
            Run.tenant_id == event.tenant_id,
            Run.project_id == event.payload.get("project_id"),
        ))
        if run is None:
            event.status = "failed"
            db.commit()
            continue
        body = json.dumps({
            "version": 1, "event_id": event.id,
            "tenant_id": event.tenant_id, "run_id": run.id,
        }, sort_keys=True, separators=(",", ":"))
        response = sqs.send_message(QueueUrl=queue_url, MessageBody=body)
        if not response.get("MessageId"):
            db.rollback()
            raise ServiceError("QUEUE_OUTCOME_UNKNOWN", "Media SQS receipt is missing", 503)
        event.queue_published_at = utcnow()
        db.commit()
        sent += 1
    return sent


def consume_one_media_dispatch(
    session_factory, sqs, queue_url: str, process_event,
    *, wait_seconds: int = 20,
) -> bool:
    if not queue_url.startswith("https://") or not 0 <= wait_seconds <= 20:
        raise ValueError("A bounded HTTPS media queue is required")
    response = sqs.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=1,
        WaitTimeSeconds=wait_seconds, VisibilityTimeout=90,
    )
    messages = response.get("Messages", [])
    if not messages:
        return False
    message = messages[0]
    payload = _valid_message(message.get("Body", ""))
    receipt = message.get("ReceiptHandle")
    if payload is None or not isinstance(receipt, str) or not receipt:
        return False
    with session_factory() as db:
        event = db.get(OutboxEvent, payload["event_id"])
        if (
            event is None or event.topic != "media.transcode"
            or event.tenant_id != payload["tenant_id"]
            or event.payload.get("run_id") != payload["run_id"]
        ):
            return False
        status = event.status
        trace_carrier = dict(event.payload)
        expired = (
            status == "processing" and event.processing_lease_until is not None
            and aware(event.processing_lease_until) <= utcnow()
        )
    if status == "pending" or expired:
        with _VisibilityHeartbeat(sqs, queue_url, receipt):
            with tracer.start_as_current_span(
                "media.consume", context=extract_trace(trace_carrier)
            ):
                set_safe_attributes(run_id=payload["run_id"], event_id=event.id)
                process_event(event.id)
        with session_factory() as db:
            current = db.get(OutboxEvent, event.id)
            status = current.status if current else "missing"
    if status not in {"delivered", "failed"}:
        return False
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    return True
