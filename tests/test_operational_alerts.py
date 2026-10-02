from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
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
    Task,
    Tenant,
)
from platform_app.operational_alerts import evaluate_tenant_alerts, resolve_alert


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


def _run(db: Session, tenant_id: str, now: datetime) -> Run:
    db.add(Tenant(id=tenant_id, name=tenant_id))
    db.add(Project(id=f"{tenant_id}-project", tenant_id=tenant_id, name="Project"))
    db.add(Task(
        id=f"{tenant_id}-task", tenant_id=tenant_id,
        project_id=f"{tenant_id}-project", report="Bug",
        expected_behavior="pass", actual_behavior="fail", created_by="owner",
    ))
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
