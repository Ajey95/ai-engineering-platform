"""At-least-once signed webhook delivery of persisted operational transitions."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.models import OperationalAlert, OperationalAlertEvent
from platform_app.ops_alerts import ALERTS


def validate_pager_destination(url: str, secret: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.fragment or parsed.port not in {None, 443}
        or len(secret.encode("utf-8")) < 32
    ):
        raise ValueError("Pager requires an HTTPS destination and a 32-byte secret")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Pager destination cannot be local")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Pager destination must use a configured DNS name")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _body(event: OperationalAlertEvent, alert: OperationalAlert) -> bytes:
    spec = ALERTS[alert.alert_id]
    payload = {
        "schema_version": "1.0", "event_id": event.id,
        "tenant_id": event.tenant_id, "alert_id": alert.alert_id,
        "generation": event.generation, "state": event.state,
        "severity": spec.severity, "owner": spec.owner,
        "impact": spec.impact, "runbook": spec.runbook,
        "observed_at": event.created_at.isoformat(), "evidence": event.evidence,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def deliver_next(
    db: Session, url: str, secret: str, *, client: httpx.Client | None = None,
    now: datetime | None = None,
) -> str | None:
    """Deliver one locked event; caller commits after response or recorded retry."""
    validate_pager_destination(url, secret)
    now = now or utcnow()
    event = db.scalar(select(OperationalAlertEvent).where(
        OperationalAlertEvent.notification_status == "pending",
        or_(
            OperationalAlertEvent.notification_next_attempt_at.is_(None),
            OperationalAlertEvent.notification_next_attempt_at <= now,
        ),
    ).order_by(
        OperationalAlertEvent.created_at, OperationalAlertEvent.id,
    ).with_for_update(skip_locked=True).limit(1))
    if event is None:
        return None
    alert = db.get(OperationalAlert, event.alert_id)
    if alert is None or alert.tenant_id != event.tenant_id:
        raise RuntimeError("Alert transition scope is inconsistent")
    body = _body(event, alert)
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AIP-Signature": f"sha256={signature}",
        "Idempotency-Key": event.id,
    }
    owned_client = client is None
    client = client or httpx.Client()
    error_code = None
    try:
        try:
            response = client.post(
                url, content=body, headers=headers, timeout=10, follow_redirects=False
            )
            if not 200 <= response.status_code < 300:
                error_code = f"HTTP_{response.status_code}"
        except httpx.TimeoutException:
            error_code = "TIMEOUT"
        except httpx.TransportError:
            error_code = "TRANSPORT"
    finally:
        if owned_client:
            client.close()
    event.notification_attempts += 1
    if error_code is None:
        event.notification_status = "delivered"
        event.notification_delivered_at = now
        event.notification_next_attempt_at = None
        event.notification_last_error = None
    else:
        event.notification_last_error = error_code
        delay = min(3600, 2 ** min(event.notification_attempts, 12))
        event.notification_next_attempt_at = now + timedelta(seconds=delay)
    return event.id
