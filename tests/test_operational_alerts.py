from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import (
    OperationalAlert,
    OperationalAlertEvent,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    SandboxLease,
    Task,
    Tenant,
)
from platform_app.operational_alerts import evaluate_tenant_alerts, resolve_alert


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _run(db: Session, tenant_id: str, now: datetime) -> Run:
    db.add(Tenant(id=tenant_id, name=tenant_id))
    db.flush()
    db.add(Project(id=f"{tenant_id}-project", tenant_id=tenant_id, name="Project"))
    db.flush()
    db.add(Task(
        id=f"{tenant_id}-task", tenant_id=tenant_id,
        project_id=f"{tenant_id}-project", report="Bug",
        expected_behavior="pass", actual_behavior="fail", created_by="owner",
    ))
    db.flush()
    run = Run(
        id=f"{tenant_id}-run", tenant_id=tenant_id, project_id=f"{tenant_id}-project",
        task_id=f"{tenant_id}-task", created_by="owner", idempotency_key=tenant_id,
        request_hash="a" * 64, base_commit="b" * 40, model_entry_id="fixture",
        state="QUEUED", verdict="NOT_RUN", config_snapshot={},
        created_at=now - timedelta(minutes=7),
    )
    db.add(run)
    db.commit()
    return run


def test_queue_requires_continuous_samples_and_transitions_once():
    engine = _database()
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with Session(engine) as db:
        run = _run(db, "a", now)
        _run(db, "b", now)
        for minute in range(11):
            current = now + timedelta(minutes=minute)
            evaluate_tenant_alerts(db, "a", now=current)
            db.commit()
            alert = db.scalar(select(OperationalAlert).where(
                OperationalAlert.tenant_id == "a"
            ))
            assert alert.state == ("firing" if minute == 10 else "observing")
        evaluate_tenant_alerts(db, "a", now=now + timedelta(minutes=10))
        db.commit()
        assert db.scalar(select(func.count(OperationalAlertEvent.id)).where(
            OperationalAlertEvent.tenant_id == "a"
        )) == 2
        assert not db.scalars(select(OperationalAlert).where(
            OperationalAlert.tenant_id == "b"
        )).all()
        run.state = "COMPLETED"
        evaluate_tenant_alerts(db, "a", now=now + timedelta(minutes=11))
        db.commit()
        assert alert.state == "resolved"
        run.state = "QUEUED"
        evaluate_tenant_alerts(db, "a", now=now + timedelta(minutes=12))
        db.commit()
        assert alert.state == "observing" and alert.generation == 2
        evaluate_tenant_alerts(db, "a", now=now + timedelta(minutes=14))
        db.commit()
        assert alert.first_seen_at.replace(tzinfo=UTC) == now + timedelta(minutes=14)
        assert alert.state == "observing"
    engine.dispose()


def test_graph_fires_immediately_and_budget_breach_stays_until_resolution():
    engine = _database()
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with Session(engine) as db:
        run = _run(db, "a", now)
        run.state = "COMPLETED"
        db.add(OutboxEvent(
            tenant_id="a", topic="memory.project", payload={"fact_id": "x"},
            status="pending", created_at=now - timedelta(seconds=90),
        ))
        db.add(RunEvent(
            id="breach-1", tenant_id="a", run_id=run.id, sequence=1,
            event_type="budget.breached", payload={"scope": "run"}, created_at=now,
        ))
        db.commit()
        evaluate_tenant_alerts(db, "a", now=now)
        db.commit()
        alerts = {row.alert_id: row for row in db.scalars(select(OperationalAlert)).all()}
        assert alerts["graph_projection_over_60_seconds"].state == "firing"
        budget = alerts["budget_breach"]
        assert budget.state == "firing"
        assert budget.evidence == {"run_id": run.id, "event_id": "breach-1"}
        outbox = db.scalar(select(OutboxEvent))
        outbox.status = "delivered"
        evaluate_tenant_alerts(db, "a", now=now + timedelta(seconds=30))
        db.commit()
        assert alerts["graph_projection_over_60_seconds"].state == "resolved"
        assert budget.state == "firing"
        resolve_alert(db, "a", budget.id, "owner", "Provider usage reconciled", now=now)
        db.commit()
        assert budget.state == "resolved"
        evaluate_tenant_alerts(db, "a", now=now + timedelta(seconds=60))
        db.commit()
        assert budget.state == "resolved"
        db.add(RunEvent(
            id="breach-2", tenant_id="a", run_id=run.id, sequence=2,
            event_type="budget.breached", payload={"scope": "tenant"},
            created_at=now + timedelta(seconds=90),
        ))
        db.commit()
        evaluate_tenant_alerts(db, "a", now=now + timedelta(seconds=90))
        db.commit()
        assert budget.state == "firing" and budget.generation == 2
        assert budget.source_cursor == "breach-2"
    engine.dispose()


def test_alert_transition_cannot_claim_another_tenant():
    engine = _database()
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with Session(engine) as db:
        db.add_all([Tenant(id="a", name="A"), Tenant(id="b", name="B")])
        db.flush()
        alert = OperationalAlert(
            tenant_id="a", alert_id="budget_breach", state="firing",
            generation=1, first_seen_at=now, last_observed_at=now, evidence={},
        )
        db.add(alert)
        db.flush()
        db.add(OperationalAlertEvent(
            alert_id=alert.id, tenant_id="b", generation=1, state="firing",
            actor="operator", reason="wrong tenant", evidence={}, created_at=now,
        ))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    engine.dispose()


def test_media_and_expired_sandbox_warnings_are_tenant_scoped_and_resolve():
    engine = _database()
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with Session(engine) as db:
        run = _run(db, "a", now)
        _run(db, "b", now)
        run.state = "COMPLETED"
        media = OutboxEvent(
            tenant_id="a", topic="media.transcode", payload={"run_id": run.id},
            status="processing", created_at=now - timedelta(minutes=11),
        )
        lease = SandboxLease(
            tenant_id="a", project_id="a-project", run_id=run.id,
            generation=1, phase="baseline", lease_fence=1, client_token="client-a",
            state="terminating", image_id="ami-123", instance_type="t3.medium",
            subnet_id="subnet-123", security_group_id="sg-123",
            root_device_name="/dev/sda1", disk_gib=20,
            expires_at=now - timedelta(minutes=6),
        )
        db.add_all([media, lease])
        db.commit()
        evaluate_tenant_alerts(db, "a", now=now)
        db.commit()
        alerts = {row.alert_id: row for row in db.scalars(select(OperationalAlert).where(
            OperationalAlert.tenant_id == "a"
        )).all()}
        assert alerts["media_encode_age"].state == "firing"
        assert alerts["sandbox_orphan"].state == "firing"
        assert alerts["sandbox_orphan"].evidence["expired_lease_count"] == 1
        assert not db.scalars(select(OperationalAlert).where(
            OperationalAlert.tenant_id == "b"
        )).all()
        media.status = "delivered"
        lease.state = "terminated"
        evaluate_tenant_alerts(db, "a", now=now + timedelta(seconds=30))
        db.commit()
        assert alerts["media_encode_age"].state == "resolved"
        assert alerts["sandbox_orphan"].state == "resolved"
    engine.dispose()


def test_provider_warning_requires_twenty_calls_and_sustained_error_samples():
    engine = _database()
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    with Session(engine) as db:
        run = _run(db, "a", now)
        _run(db, "b", now)
        run.state = "COMPLETED"
        sequence = 0
        for minute in range(6):
            observed = now + timedelta(minutes=minute)
            for number in range(20):
                if minute == 0 and number == 19:
                    db.flush()
                    evaluate_tenant_alerts(db, "a", now=observed)
                    assert db.scalar(select(OperationalAlert).where(
                        OperationalAlert.tenant_id == "a",
                        OperationalAlert.alert_id == "provider_error_rate",
                    )) is None
                sequence += 1
                step = f"model-{minute}-{number}"
                db.add(RunEvent(
                    tenant_id="a", run_id=run.id, sequence=sequence,
                    event_type="model.started", payload={"step_id": step},
                    created_at=observed - timedelta(seconds=1),
                ))
                sequence += 1
                db.add(RunEvent(
                    tenant_id="a", run_id=run.id, sequence=sequence,
                    event_type="model.rejected" if number < 5 else "model.completed",
                    payload={"step_id": step}, created_at=observed,
                ))
            db.flush()
            evaluate_tenant_alerts(db, "a", now=observed)
            db.commit()
            alert = db.scalar(select(OperationalAlert).where(
                OperationalAlert.tenant_id == "a",
                OperationalAlert.alert_id == "provider_error_rate",
            ))
            assert alert.state == ("firing" if minute == 5 else "observing")
        assert alert.evidence["sampled_calls"] >= 20
        assert alert.evidence["error_rate"] == 0.25
        assert not db.scalars(select(OperationalAlert).where(
            OperationalAlert.tenant_id == "b"
        )).all()
        evaluate_tenant_alerts(db, "a", now=now + timedelta(minutes=12))
        db.commit()
        assert alert.state == "resolved"
    engine.dispose()
