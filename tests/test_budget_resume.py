from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app import run_ledger
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
    ToolAction,
)
from platform_app.service import ServiceError


@pytest.fixture
def paused(monkeypatch):
    monkeypatch.setattr(run_ledger, "qualification_for_pinned_run", lambda model: True)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Tenant(id="tenant", name="Synthetic"),
            Project(id="project", tenant_id="tenant", name="Synthetic"),
            Task(
                id="task", tenant_id="tenant", project_id="project", created_by="owner",
                report="Bug", expected_behavior="Works", actual_behavior="Fails",
            ),
            ModelEntry(
                id="model", provider="openai", model_id="test", state="enabled",
                registry_revision="rev", context_limit=32000, output_limit=1000,
                price_revision="price", price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"), capabilities={},
            ),
            Run(
                id="run", tenant_id="tenant", project_id="project", task_id="task",
                created_by="owner", idempotency_key="admit", request_hash="a" * 64,
                base_commit="b" * 40, model_entry_id="model", state="PAUSED_BUDGET",
                resume_target="PATCHING", config_snapshot={
                    "policy_version": "1.0", "spend_limit_usd": "1.000000",
                }, last_sequence=1,
            ),
            BudgetEntry(
                tenant_id="tenant", run_id="run", category="run_cap",
                reserved_usd=Decimal("1"),
            ),
            RunEvent(
                tenant_id="tenant", run_id="run", sequence=1,
                event_type="budget.pause", payload={"spend_limit_usd": "1.000000"},
            ),
            OutboxEvent(tenant_id="tenant", topic="run.dispatch", payload={"run_id": "run"}),
        ])
        db.commit()
        yield db
    engine.dispose()


def approve(db: Session, limit: str = "2.000000", key: str = "budget-key-001") -> Run:
    return run_ledger.resume_budget_run(
        db, "tenant", "run", "owner", "Reviewed new run budget", Decimal(limit), key,
    )


def test_budget_resume_updates_cap_and_dispatches_once(paused):
    run = approve(paused)
    paused.commit()
    assert run.state == "QUEUED"
    assert run.config_snapshot["spend_limit_usd"] == "2.000000"
    assert Decimal(paused.scalar(select(BudgetEntry.reserved_usd))) == Decimal("2")
    assert [row.status for row in paused.scalars(select(OutboxEvent).order_by(
        OutboxEvent.created_at
    ))] == ["delivered", "pending"]
    assert approve(paused).id == run.id
    paused.commit()
    assert paused.query(OutboxEvent).count() == 2
    assert paused.query(RunEvent).filter_by(event_type="budget.approved").count() == 1
    with pytest.raises(ServiceError) as conflict:
        approve(paused, "3.000000")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_budget_resume_refuses_cap_policy_uncertain_effect_and_expiry(paused):
    with pytest.raises(ServiceError) as cap:
        approve(paused, "6.000000")
    assert cap.value.code == "RUN_BUDGET_CAP"
    paused.rollback()
    paused.get(Tenant, "tenant").policy_revision = "2.0"
    paused.commit()
    with pytest.raises(ServiceError) as policy:
        approve(paused)
    assert policy.value.code == "POLICY_REVIEW_REQUIRED"
    paused.rollback()
    paused.get(Tenant, "tenant").policy_revision = "1.0"
    paused.add(ToolAction(
        tenant_id="tenant", run_id="run", step_id="model-a",
        logical_action="model.generate", effect_key="e" * 64,
        arguments_hash="a" * 64, policy_result="allowed", status="INTENDED",
    ))
    paused.commit()
    with pytest.raises(ServiceError) as uncertain:
        approve(paused)
    assert uncertain.value.code == "EFFECT_OUTCOME_UNKNOWN"
    paused.rollback()
    paused.query(ToolAction).one().status = "COMPLETED"
    paused.query(RunEvent).filter_by(event_type="budget.pause").one().created_at = (
        datetime.now(UTC) - timedelta(hours=25)
    )
    paused.commit()
    with pytest.raises(ServiceError) as expired:
        approve(paused)
    assert expired.value.code == "APPROVAL_EXPIRED"
    paused.rollback()
    assert run_ledger.expire_budget_pauses(paused, "tenant") == 1
    paused.commit()
    assert paused.get(Run, "run").state == "CANCELLED"
    assert run_ledger.expire_budget_pauses(paused, "tenant") == 0
