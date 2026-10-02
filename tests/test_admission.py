from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import service
from platform_app.api import list_models
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import (
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Project,
    RepositoryConnection,
    Run,
    RunEvent,
    Task,
    Tenant,
)
from platform_app.run_ledger import claim_run, transition
from platform_app.schemas import RunCreate
from platform_app.service import ServiceError, admit_run, request_cancel
from platform_app.tenant_quota import QuotaError, check_admission_quota, lock_tenant


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
                    environment_manifest={
                        "case_id": "form-submit-001",
                        "named_tests": {"unit": ["pytest", "-q"]},
                    },
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
                    capabilities={"database_fixture_only": True},
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
    return RunCreate(
        base_commit="a" * 40,
        selected_model_entry="qualified-model",
        reproduction={"fixture_case_id": "form-submit-001"},
    )


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


def test_tenant_concurrent_run_cap_allows_idempotent_retry_and_releases_on_close(db):
    tenant = db.get(Tenant, "tenant-a")
    tenant.max_concurrent_runs = 1
    db.commit()
    first = admit_run(db, "tenant-a", "alice", "task-a", "first-key", run_body())
    db.commit()
    assert admit_run(db, "tenant-a", "alice", "task-a", "first-key", run_body()).id == first.id
    with pytest.raises(ServiceError) as error:
        admit_run(db, "tenant-a", "alice", "task-a", "second-key", run_body())
    assert error.value.code == "RUN_CONCURRENCY_EXHAUSTED"
    first.state = "COMPLETED"
    db.commit()
    second = admit_run(db, "tenant-a", "alice", "task-a", "second-key", run_body())
    db.commit()
    assert second.id != first.id


def test_tenant_spend_cap_blocks_new_admission(db):
    tenant = db.get(Tenant, "tenant-a")
    tenant.daily_inference_cap_usd = Decimal("0.01")
    tenant.monthly_inference_cap_usd = Decimal("0.02")
    run = admit_run(db, "tenant-a", "alice", "task-a", "first-key", run_body())
    db.flush()
    db.add(
        BudgetEntry(
            tenant_id=tenant.id,
            run_id=run.id,
            category="call:old",
            reserved_usd=Decimal("0.01"),
            actual_usd=Decimal("0"),
            status="reserved",
        )
    )
    db.commit()
    with pytest.raises(ServiceError) as error:
        admit_run(db, "tenant-a", "alice", "task-a", "second-key", run_body())
    assert error.value.code == "TENANT_BUDGET_EXHAUSTED"


def test_locked_tenant_refreshes_cached_quota(db):
    tenant = db.get(Tenant, "tenant-a")
    assert Decimal(tenant.daily_inference_cap_usd) == Decimal("50")
    db.execute(
        text("UPDATE tenants SET daily_inference_cap_usd = :amount WHERE id = :tenant_id"),
        {"amount": "0.015", "tenant_id": tenant.id},
    )
    assert Decimal(lock_tenant(db, tenant.id).daily_inference_cap_usd) == Decimal("0.015")


def test_utc_inference_periods_do_not_charge_next_month(db):
    run = admit_run(db, "tenant-a", "alice", "task-a", "first-key", run_body())
    tenant = db.get(Tenant, "tenant-a")
    tenant.daily_inference_cap_usd = Decimal("0.01")
    tenant.monthly_inference_cap_usd = Decimal("0.01")
    db.flush()
    db.add(BudgetEntry(
        tenant_id=tenant.id, run_id=run.id, category="call:september",
        reserved_usd=Decimal("0.015"), actual_usd=Decimal(0), status="reserved",
        created_at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC),
    ))
    db.commit()
    with pytest.raises(QuotaError):
        check_admission_quota(db, tenant, datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    check_admission_quota(db, tenant, datetime(2026, 10, 1, 0, 0, tzinfo=UTC))


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


def test_enabled_unqualified_model_cannot_admit_general_run(db):
    body = RunCreate(base_commit="a" * 40, selected_model_entry="qualified-model")
    with pytest.raises(ServiceError) as error:
        admit_run(db, "tenant-a", "alice", "task-a", "unqualified-key", body)
    assert error.value.code == "MODEL_UNAVAILABLE"


def test_hosted_admission_requires_gate_scope_and_ready_connection(db, monkeypatch):
    disabled = Settings(
        environment="production", hosted_execution_enabled=False,
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/", oidc_audience="aip",
        oidc_jwks_url="https://issuer.example.test/keys",
    )
    enabled = disabled.model_copy(update={"hosted_execution_enabled": True})
    monkeypatch.setattr(service, "settings", lambda: disabled)
    monkeypatch.setattr(service, "qualification_current", lambda _: True)
    project = db.get(Project, "project-a")
    project.repository_url = "https://github.com/example/repo"
    project.environment_manifest = {
        "schema_version": "1.0", "language": "python", "python_version": "3.12",
        "services": [{
            "name": "app", "port": 8001, "health_path": "/health",
            "command": {"argv": ["python", "app.py"], "timeout_seconds": 30},
        }],
        "named_tests": {"unit": {
            "argv": ["python", "-c", "print('ok')"], "timeout_seconds": 30,
        }},
        "browser_scenario": {"steps": [{"action": "goto", "path": "/"}]},
    }
    db.commit()
    body = RunCreate(
        base_commit="a" * 40, selected_model_entry="qualified-model",
        reproduction={"execution_profile": "hosted_vm_v1", "repair_paths": ["app.py"]},
    )
    with pytest.raises(ServiceError) as rejected:
        admit_run(db, "tenant-a", "alice", "task-a", "hosted-key", body)
    assert rejected.value.code == "EXECUTION_UNAVAILABLE"
    monkeypatch.setattr(service, "settings", lambda: enabled)
    with pytest.raises(ServiceError) as missing:
        admit_run(db, "tenant-a", "alice", "task-a", "hosted-key", body)
    assert missing.value.code == "REPOSITORY_UNAVAILABLE"
    db.add(RepositoryConnection(
        tenant_id="tenant-a", project_id="project-a", provider="github",
        repository_ref="example/repo", credential_ref="env:AIP_TEST_GITHUB_TOKEN",
        status="ready", created_by="alice",
    ))
    db.commit()
    unsafe = body.model_copy(update={"reproduction": {
        "execution_profile": "hosted_vm_v1", "repair_paths": ["../secrets"],
    }})
    with pytest.raises(ServiceError) as invalid:
        admit_run(db, "tenant-a", "alice", "task-a", "unsafe-key", unsafe)
    assert invalid.value.code == "REPAIR_SCOPE_INVALID"
    run = admit_run(db, "tenant-a", "alice", "task-a", "hosted-key", body)
    assert run.config_snapshot["execution_profile"] == "hosted_vm_v1"
    assert run.config_snapshot["repair_paths"] == ["app.py"]


def test_fixture_model_cannot_admit_unrelated_project(db):
    project = db.get(Project, "project-a")
    project.environment_manifest = {"case_id": "another-case"}
    db.commit()
    with pytest.raises(ServiceError) as error:
        admit_run(db, "tenant-a", "alice", "task-a", "unrelated-key", run_body())
    assert error.value.code == "MODEL_UNAVAILABLE"


def test_fixture_model_is_not_reported_as_qualified(db):
    entries = list_models(identity=("tenant-a", "alice"), db=db)
    assert entries[0]["state"] == "enabled"
    assert entries[0]["fixture_only"] is True
    assert entries[0]["qualified"] is False


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


def test_paused_cancellation_closes_without_a_worker(db):
    run = admit_run(db, "tenant-a", "alice", "task-a", "paused-cancel-key", run_body())
    run.state = "PAUSED_INPUT"
    db.commit()
    request_cancel(db, run, "alice")
    db.commit()
    assert run.state == "CANCELLED"
    assert run.verdict == "NOT_RUN"


def test_active_cancellation_waits_for_worker_but_expired_lease_closes(db):
    run = admit_run(db, "tenant-a", "alice", "task-a", "active-cancel-key", run_body())
    db.commit()
    _, fence = claim_run(db, run.id, "worker-one")
    transition(db, run, "worker-one", fence, "PREPARING")
    db.commit()
    request_cancel(db, run, "alice")
    db.commit()
    assert run.state == "CANCEL_REQUESTED"

    run.lease_until = None
    request_cancel(db, run, "alice")
    db.commit()
    assert run.state == "CANCELLED"
