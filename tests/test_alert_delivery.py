import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.alert_delivery import deliver_next, validate_pager_destination
from platform_app.db import Base
from platform_app.models import OperationalAlert, OperationalAlertEvent, Tenant


def test_alert_webhook_retries_and_uses_signed_idempotent_transition():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    secret = "s" * 32
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 204, text="private response")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with Session(engine) as db:
        db.add(Tenant(id="a", name="A"))
        db.flush()
        alert = OperationalAlert(
            tenant_id="a", alert_id="budget_breach", state="firing", generation=1,
            first_seen_at=now, last_observed_at=now, evidence={"run_id": "run-a"},
        )
        db.add(alert)
        db.flush()
        event = OperationalAlertEvent(
            id="event-a", tenant_id="a", alert_id=alert.id, generation=1,
            state="firing", actor="monitor", reason="budget breach",
            evidence={"run_id": "run-a"}, created_at=now,
            notification_status="pending", notification_next_attempt_at=now,
        )
        db.add(event)
        db.commit()
        url = "https://pager.example.test/ingest"
        assert deliver_next(db, url, secret, client=client, now=now) == event.id
        db.commit()
        assert event.notification_status == "pending"
        assert event.notification_last_error == "HTTP_503"
        assert event.notification_attempts == 1
        assert deliver_next(db, url, secret, client=client, now=now) is None
        assert deliver_next(
            db, url, secret, client=client, now=now + timedelta(seconds=2)
        ) == event.id
        db.commit()
        assert event.notification_status == "delivered"
        assert event.notification_attempts == 2
        assert deliver_next(
            db, url, secret, client=client, now=now + timedelta(minutes=1)
        ) is None
    assert len(requests) == 2
    assert all(request.headers["Idempotency-Key"] == "event-a" for request in requests)
    assert requests[0].content == requests[1].content
    assert requests[0].headers["X-AIP-Signature"] == "sha256=" + hmac.new(
        secret.encode(), requests[0].content, hashlib.sha256
    ).hexdigest()
    payload = json.loads(requests[0].content)
    assert payload["tenant_id"] == "a"
    assert payload["state"] == "firing"
    assert "private response" not in json.dumps(payload)
    assert secret not in json.dumps(payload)
    client.close()
    engine.dispose()


@pytest.mark.parametrize("url", [
    "http://pager.example.test/ingest",
    "https://localhost/ingest",
    "https://127.0.0.1/ingest",
    "https://user:password@pager.example.test/ingest",
    "https://pager.example.test:8443/ingest",
])
def test_pager_rejects_unsafe_destination(url):
    with pytest.raises(ValueError):
        validate_pager_destination(url, "s" * 32)
