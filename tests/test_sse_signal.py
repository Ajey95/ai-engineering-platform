import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api
from platform_app.db import Base
from platform_app.event_signal import EventSignal
from platform_app.models import (
    Project,
    ProjectMembership,
    Run,
    Task,
    Tenant,
    TenantMembership,
)
from platform_app.service import ServiceError


def test_signal_wakes_only_current_run_subscribers():
    async def scenario():
        signal = EventSignal("sqlite://")
        first = signal.subscribe("run-a")
        second = signal.subscribe("run-a")
        unrelated = signal.subscribe("run-b")
        signal.notify("run-a")
        assert first.is_set() and second.is_set() and not unrelated.is_set()
        signal.unsubscribe("run-a", first)
        first.clear()
        signal.notify("run-a")
        assert not first.is_set()
        signal.unsubscribe("run-a", second)
        signal.unsubscribe("run-b", unrelated)
        assert not signal._subscribers

    asyncio.run(scenario())


def test_sse_access_rechecks_tenant_project_and_membership(monkeypatch):
    monkeypatch.setattr(api, "settings", lambda: SimpleNamespace(environment="production"))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(id="tenant-a", name="A")
        project = Project(id="project-a", tenant_id="tenant-a", name="A")
        task = Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a", report="bug",
            expected_behavior="works", actual_behavior="fails", created_by="author",
        )
        run = Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a", task_id="task-a",
            created_by="author", idempotency_key="admit-a", request_hash="a" * 64,
            base_commit="b" * 40, model_entry_id="model-a", config_snapshot={},
        )
        member = TenantMembership(tenant_id="tenant-a", subject="alice", role="member")
        project_member = ProjectMembership(
            tenant_id="tenant-a", project_id="project-a", subject="alice", role="viewer"
        )
        db.add_all([tenant, project, task, run, member, project_member])
        db.commit()
        api.sse_authorized_run(db, ("tenant-a", "alice"), "run-a")
        for identity, run_id in [("tenant-b", "run-a"), ("tenant-a", "run-b")]:
            with pytest.raises(ServiceError):
                api.sse_authorized_run(db, (identity, "alice"), run_id)
        project_member.status = "disabled"
        db.commit()
        with pytest.raises(ServiceError):
            api.sse_authorized_run(db, ("tenant-a", "alice"), "run-a")
        member.role = "owner"
        db.commit()
        api.sse_authorized_run(db, ("tenant-a", "alice"), "run-a")
        member.status = "disabled"
        db.commit()
        with pytest.raises(ServiceError):
            api.sse_authorized_run(db, ("tenant-a", "alice"), "run-a")
    engine.dispose()
