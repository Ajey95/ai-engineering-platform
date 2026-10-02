"""At-least-once SQS wakeup handling around the canonical dispatch ledger."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from platform_app.models import OutboxEvent


def _valid_message(body: str) -> dict | None:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "version", "event_id", "tenant_id", "run_id"
    } or payload["version"] != 1:
        return None
    if any(not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 80
           for key in ("event_id", "tenant_id", "run_id")):
        return None
    return payload


class _VisibilityHeartbeat:
    def __init__(self, sqs, queue_url: str, receipt: str):
        self.sqs = sqs
        self.queue_url = queue_url
        self.receipt = receipt
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(30):
            try:
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url, ReceiptHandle=self.receipt,
                    VisibilityTimeout=90,
                )
            except Exception:
                # The database lease and effect ledger still fence work. The
                # message can reappear and another consumer must recheck them.
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=2)


def consume_one_run_dispatch(
    session_factory: Callable[[], Session], sqs, queue_url: str,
    process_event: Callable[[str], str | None],
    *, wait_seconds: int = 20,
) -> bool:
    """Delete only a terminal ledger event; duplicates never authorize a new run."""
    if not queue_url.startswith("https://") or not 0 <= wait_seconds <= 20:
        raise ValueError("An HTTPS queue URL and bounded wait are required")
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
            event is None or event.topic != "run.dispatch"
            or event.tenant_id != payload["tenant_id"]
            or event.payload.get("run_id") != payload["run_id"]
        ):
            return False
        status = event.status
    if status == "pending":
        with _VisibilityHeartbeat(sqs, queue_url, receipt):
            process_event(payload["event_id"])
        with session_factory() as db:
            event = db.get(OutboxEvent, payload["event_id"])
            status = event.status if event else "missing"
    if status not in {"delivered", "failed"}:
        return False
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    return True
