from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import (
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
)
from platform_app.schemas import RunCreate
from platform_app.service import ServiceError, admit_run, request_cancel


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="A"),
                Tenant(id="tenant-b", name="B"),
                Project(
                    id="project-a",
                    tenant_id="tenant-a",
                    name="Fixture",
                    repository_url="https://example.test/repo.git",
                    test_url="http://fixture.test",
                    environment_manifest={"named_tests": {"unit": ["pytest", "-q"]}},
                ),
                Task(
                    id="task-a",
                    tenant_id="tenant-a",
                    project_id="project-a",
                    report="Submitting a valid form returns 500",
                    expected_behavior="Created",
                    actual_behavior="500",
                    created_by="alice",
                ),
                ModelEntry(
                    id="qualified-model",
                    provider="openai",
                    model_id="fixture-model",
                    registry_revision="rev-1",
                    state="enabled",
                    context_limit=32000,
                    output_limit=4000,
                    price_revision="price-1",
                    price_per_m_input=Decimal("1"),
                    price_per_m_output=Decimal("2"),
                ),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def run_body():
    return RunCreate(base_commit="a" * 40, selected_model_entry="qualified-model")


def test_admission_is_atomic_and_idempotent(db):
    first = admit_run(db, "tenant-a", "alice", "task-a", "same-request-key", run_body())
    db.commit()
    second = admit_run(db, "tenant-a", "alice", "task-a", "same-request-key", run_body())
    db.commit()
    assert first.id == second.id
    assert len(db.scalars(select(Run)).all()) == 1
    assert len(db.scalars(select(BudgetEntry)).all()) == 1
    assert len(db.scalars(select(OutboxEvent)).all()) == 1
    assert len(db.scalars(select(RunEvent)).all()) == 1


def test_idempotency_key_cannot_be_reused_for_different_request(db):
    admit_run(db, "tenant-a", "alice", "task-a", "same-request-key", run_body())
    db.commit()
    changed = RunCreate(base_commit="b" * 40, selected_model_entry="qualified-model")
    with pytest.raises(ServiceError, match="different request") as error:
        admit_run(db, "tenant-a", "alice", "task-a", "same-request-key", changed)
    assert error.value.status == 409


def test_cross_tenant_task_is_opaque(db):
    with pytest.raises(ServiceError) as error:
        admit_run(db, "tenant-b", "bob", "task-a", "another-request-key", run_body())
    assert error.value.status == 404


def test_queued_cancellation_does_not_become_success(db):
    run = admit_run(db, "tenant-a", "alice", "task-a", "cancel-request-key", run_body())
    request_cancel(db, run, "alice")
    db.commit()
    assert run.state == "CANCELLED"
    assert run.verdict == "NOT_RUN"
    assert [e.event_type for e in db.scalars(select(RunEvent).order_by(RunEvent.sequence))] == [
        "run.admitted",
        "run.closed",
    ]
