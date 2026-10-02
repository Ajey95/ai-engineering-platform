from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.export_quota import reserve_export
from platform_app.models import AuditEvent, ExportCharge, Project, Run, Task, Tenant
from platform_app.tenant_quota import QuotaError


def test_daily_export_cap_enforces_limit_and_resets_at_utc_midnight():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant", name="A", daily_export_cap_bytes=100))
        db.add(Project(id="project", tenant_id="tenant", name="P"))
        db.add(Task(
            id="task", tenant_id="tenant", project_id="project",
            report="Bug", expected_behavior="works", actual_behavior="fails",
            created_by="actor",
        ))
        db.add(Run(
            id="run", tenant_id="tenant", project_id="project", task_id="task",
            created_by="actor", idempotency_key="key", request_hash="a" * 64,
            base_commit="b" * 40, model_entry_id="fixture", config_snapshot={},
        ))
        db.commit()
        before_midnight = datetime(2026, 10, 2, 23, 59, tzinfo=UTC)
        reserve_export(db, "tenant", "run", "actor", 79, "a" * 64,
                       now=before_midnight)
        db.commit()
        reserve_export(db, "tenant", "run", "actor", 1, "b" * 64,
                       now=before_midnight + timedelta(seconds=1))
        db.commit()
        assert db.query(AuditEvent).filter_by(action="export.threshold_80").count() == 1
        with pytest.raises(QuotaError, match="daily export cap"):
            reserve_export(db, "tenant", "run", "actor", 21, "c" * 64,
                           now=before_midnight + timedelta(seconds=1))
        assert db.query(ExportCharge).count() == 2
        reserve_export(db, "tenant", "run", "actor", 100, "d" * 64,
                       now=before_midnight + timedelta(minutes=1))
        db.commit()
        assert db.query(ExportCharge).count() == 3
    engine.dispose()
