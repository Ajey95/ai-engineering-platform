from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api, auth
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import (
    BudgetEntry,
    ExportCharge,
    OperationalAlert,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
    TenantMembership,
    ToolAction,
)
from platform_app.operations import operations_snapshot


def test_operations_snapshot_scopes_aggregates_and_marks_missing_metrics():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    with Session(engine) as db:
        for tenant_id in ("tenant-a", "tenant-b"):
            db.add(Tenant(id=tenant_id, name=tenant_id))
            db.add(Project(id=f"{tenant_id}-project", tenant_id=tenant_id, name="Project"))
            db.add(Task(
                id=f"{tenant_id}-task", tenant_id=tenant_id,
                project_id=f"{tenant_id}-project", report="Bug",
                expected_behavior="pass", actual_behavior="fail", created_by="owner",
            ))
        for name, state, verdict, age, tenant_id in (
            ("queued", "QUEUED", "NOT_RUN", 420, "tenant-a"),
            ("passed", "COMPLETED", "PASSED", 30, "tenant-a"),
            ("unclear", "INCONCLUSIVE", "INCONCLUSIVE", 20, "tenant-a"),
            ("cancelled", "CANCELLED", "PASSED", 15, "tenant-a"),
            ("foreign", "COMPLETED", "FAILED", 10, "tenant-b"),
        ):
            db.add(Run(
                id=name, tenant_id=tenant_id, project_id=f"{tenant_id}-project",
                task_id=f"{tenant_id}-task", created_by="owner",
                idempotency_key=name, request_hash="a" * 64, base_commit="b" * 40,
                model_entry_id="fixture", state=state, verdict=verdict,
                media_status="READY" if name == "passed" else "PENDING",
                config_snapshot={}, created_at=now - timedelta(seconds=age),
            ))
        db.add(OutboxEvent(
            tenant_id="tenant-a", topic="memory.project", payload={"fact_id": "x"},
            status="pending", created_at=now - timedelta(seconds=90),
        ))
        db.add(ToolAction(
            tenant_id="tenant-a", run_id="passed", step_id="step",
            logical_action="test", effect_key="e" * 64, arguments_hash="f" * 64,
            policy_result="denied", status="FAILED", created_at=now,
        ))
        db.add(RunEvent(
            tenant_id="tenant-a", run_id="passed", sequence=1,
            event_type="review.decision", payload={"decision": "accepted"},
            created_at=now,
        ))
        db.add(BudgetEntry(
            tenant_id="tenant-a", run_id="passed", category="call:one",
            reserved_usd=2.5, actual_usd=1.25, status="settled", created_at=now,
        ))
        db.add(BudgetEntry(
            tenant_id="tenant-a", run_id="passed", category="call:two",
            reserved_usd=1, actual_usd=0, status="reserved", created_at=now,
        ))
        db.add(ExportCharge(
            tenant_id="tenant-a", run_id="passed", actor="owner",
            bytes_count=1234, archive_sha256="a" * 64, created_at=now,
        ))
        db.commit()
        snapshot = operations_snapshot(db, "tenant-a", now=now)
        assert snapshot["runs"]["by_state"] == {
            "QUEUED": 1, "COMPLETED": 1, "INCONCLUSIVE": 1, "CANCELLED": 1,
        }
        assert snapshot["runs"]["closed_count"] == 3
        assert snapshot["runs"]["verification_pass_rate"] == 0.3333
        assert snapshot["runs"]["inconclusive_rate"] == 0.3333
        assert snapshot["runs"]["reviewed_count"] == 1
        assert snapshot["runs"]["review_acceptance_rate"] == 1.0
        assert snapshot["queue"] == {"queued_count": 1, "oldest_age_seconds": 420}
        assert snapshot["graph"] == {"pending_count": 1, "oldest_age_seconds": 90}
        assert snapshot["tools"] == {"by_policy_result": {"denied": 1}, "failed_count": 1}
        assert snapshot["inference_budget"] == {
            "reserved_usd": "1.000000", "actual_usd": "1.250000",
        }
        assert snapshot["exports"] == {
            "used_bytes_today": 1234, "daily_cap_bytes": 100_000_000,
        }
        assert snapshot["warnings_now"] == [
            "runnable_queue_over_5_minutes", "graph_projection_over_60_seconds",
        ]
        assert [item["owner"] for item in snapshot["warning_details"]] == [
            "platform-on-call", "platform-on-call",
        ]
        assert all(item["evaluation"] == "snapshot_only"
                   for item in snapshot["warning_details"])
        assert "sandbox_utilization" in snapshot["unavailable"]
        assert operations_snapshot(db, "tenant-b", now=now)["runs"]["by_state"] == {
            "COMPLETED": 1,
        }
    engine.dispose()


def test_operations_api_requires_tenant_owner(monkeypatch):
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
    )
    monkeypatch.setattr(api, "settings", lambda: config)
    monkeypatch.setattr(auth, "settings", lambda: config)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Tenant(id="tenant-b", name="B"))
        db.add_all([
            TenantMembership(tenant_id="tenant-a", subject="alice", role="owner"),
            TenantMembership(tenant_id="tenant-a", subject="bob", role="member"),
            OperationalAlert(
                id="alert-a", tenant_id="tenant-a", alert_id="budget_breach",
                state="firing", generation=1, first_seen_at=datetime.now(UTC),
                last_observed_at=datetime.now(UTC), evidence={"event_id": "event-a"},
            ),
            OperationalAlert(
                id="alert-b", tenant_id="tenant-b", alert_id="budget_breach",
                state="firing", generation=1, first_seen_at=datetime.now(UTC),
                last_observed_at=datetime.now(UTC), evidence={"event_id": "event-b"},
            ),
        ])
        db.commit()

    def session():
        with Session(engine) as db:
            yield db

    actor = ["bob"]
    api.app.dependency_overrides[api.db_session] = session
    api.app.dependency_overrides[api.principal] = lambda: ("tenant-a", actor[0])
    try:
        client = TestClient(api.app)
        assert client.get("/v1/operations/summary").status_code == 403
        assert client.post(
            "/v1/operations/alerts/alert-a/resolve", json={"reason": "Reviewed event"}
        ).status_code == 403
        actor[0] = "alice"
        response = client.get("/v1/operations/summary")
        assert response.status_code == 200, response.text
        assert response.json()["runs"]["verification_pass_rate"] is None
        assert response.json()["runs"]["review_acceptance_rate"] is None
        assert response.json()["unavailable"]
        assert [item["id"] for item in response.json()["active_alerts"]] == ["alert-a"]
        assert client.post(
            "/v1/operations/alerts/alert-b/resolve", json={"reason": "Reviewed event"}
        ).status_code == 404
        resolved = client.post(
            "/v1/operations/alerts/alert-a/resolve", json={"reason": "Reviewed event"}
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["state"] == "resolved"
    finally:
        api.app.dependency_overrides.clear()
        engine.dispose()
