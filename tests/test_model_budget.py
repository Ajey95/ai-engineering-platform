import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.model_budget import reserve_model_call, settle_model_call
from platform_app.models import (
    BudgetEntry,
    ModelEntry,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.run_ledger import claim_run
from platform_app.service import ServiceError


@pytest.fixture
def scope():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        manifest = {"case_id": "form-submit-001"}
        db.add(
            Project(
                id="project-a",
                tenant_id="tenant-a",
                name="Fixture",
                repository_url="https://example.test/repo",
                test_url="https://example.test",
                environment_manifest=manifest,
            )
        )
        db.add(
            Task(
                id="task-a",
                tenant_id="tenant-a",
                project_id="project-a",
                report="Broken form",
                expected_behavior="Created",
                actual_behavior="500",
                created_by="alice",
            )
        )
        db.add(
            ModelEntry(
                id="model-a",
                provider="openai",
                model_id="live-model",
                registry_revision="rev-a",
                state="enabled",
                capabilities={"controlled_provider_fixture": True},
                validated_at=datetime.now(UTC),
                context_limit=32000,
                output_limit=4000,
                price_revision="price-a",
                price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"),
            )
        )
        db.add(
            Run(
                id="run-a",
                tenant_id="tenant-a",
                task_id="task-a",
                project_id="project-a",
                created_by="alice",
                idempotency_key="key-a",
                request_hash="hash",
                base_commit="a" * 40,
                model_entry_id="model-a",
                state="QUEUED",
                config_snapshot={
                    "policy_version": "1.0",
                    "reproduction": {"fixture_case_id": "form-submit-001"},
                    "environment_manifest": manifest,
                    "repository_url": "https://example.test/repo",
                    "test_url": "https://example.test",
                    "max_tool_calls": 20,
                    "max_model_calls": 1,
                    "spend_limit_usd": 5,
                    "model_registry_revision": "rev-a",
                    "model_price_revision": "price-a",
                    "model_context_limit": 32000,
                    "model_output_limit": 4000,
                    "model_price_per_m_input": "1.000000",
                    "model_price_per_m_output": "2.000000",
                },
            )
        )
        db.commit()
        run, fence = claim_run(db, "run-a", "worker-one")
        db.commit()
        yield db, run, db.get(ModelEntry, "model-a"), fence
    engine.dispose()


def test_model_reservation_precedes_settlement_and_uses_reported_usage(scope):
    db, run, model, fence = scope
    action, reservation, plan = reserve_model_call(
        db, run, "worker-one", fence, model, "model-1", "Fix the form"
    )
    db.commit()
    assert action.status == "INTENDED"
    assert reservation.status == "reserved"
    assert Decimal(reservation.reserved_usd) == plan.max_liability_usd
    assert (
        len(db.scalars(select(BudgetEntry).where(BudgetEntry.category.like("call:%"))).all()) == 1
    )

    actual = settle_model_call(
        db,
        run,
        "worker-one",
        fence,
        action,
        reservation,
        model,
        {"input_tokens": 12, "output_tokens": 30},
        hashlib.sha256(b"output").hexdigest(),
        "private/model-1.json",
    )
    db.commit()
    assert actual == Decimal("0.000072")
    assert reservation.status == "settled"
    assert action.receipt["usage"] == {"input_tokens": 12, "output_tokens": 30}
    with pytest.raises(ServiceError) as error:
        reserve_model_call(db, run, "worker-one", fence, model, "model-2", "Again")
    assert error.value.code == "BUDGET_EXHAUSTED"


def test_unqualified_model_and_uncertain_usage_fail_closed(scope):
    db, run, model, fence = scope
    model.validated_at = None
    with pytest.raises(ServiceError) as error:
        reserve_model_call(db, run, "worker-one", fence, model, "model-1", "Fix")
    assert error.value.code == "MODEL_UNAVAILABLE"
    model.validated_at = datetime.now(UTC)
    action, reservation, _ = reserve_model_call(
        db, run, "worker-one", fence, model, "model-1", "Fix"
    )
    db.commit()
    with pytest.raises(ServiceError) as error:
        settle_model_call(
            db,
            run,
            "worker-one",
            fence,
            action,
            reservation,
            model,
            {},
            hashlib.sha256(b"output").hexdigest(),
            "private/model-1.json",
        )
    assert error.value.code == "USAGE_UNKNOWN"
    with pytest.raises(ServiceError) as cached:
        settle_model_call(
            db,
            run,
            "worker-one",
            fence,
            action,
            reservation,
            model,
            {"input_tokens": 100, "output_tokens": 10, "cache_creation_tokens": 50},
            hashlib.sha256(b"output").hexdigest(),
            "private/model-1.json",
        )
    assert cached.value.code == "USAGE_PRICING_UNQUALIFIED"
    assert db.scalar(select(ToolAction.status)) == "INTENDED"


def test_model_price_revision_drift_blocks_new_call(scope):
    db, run, model, fence = scope
    model.price_revision = "price-b"
    with pytest.raises(ServiceError) as error:
        reserve_model_call(db, run, "worker-one", fence, model, "model-1", "Fix")
    assert error.value.code == "MODEL_REVISION_CHANGED"


def test_tenant_inference_cap_blocks_reservation_before_tool_intent(scope):
    db, run, model, fence = scope
    tenant = db.get(Tenant, "tenant-a")
    tenant.daily_inference_cap_usd = Decimal("0.01")
    with pytest.raises(ServiceError) as error:
        reserve_model_call(db, run, "worker-one", fence, model, "model-1", "Fix")
    assert error.value.code == "TENANT_BUDGET_EXHAUSTED"
    assert db.scalar(select(ToolAction)) is None
    assert db.scalar(select(BudgetEntry)) is None


def test_tenant_inference_80_percent_warning(scope):
    db, run, model, fence = scope
    tenant = db.get(Tenant, "tenant-a")
    tenant.daily_inference_cap_usd = Decimal("0.014")
    tenant.monthly_inference_cap_usd = Decimal("0.014")
    _, reservation, _ = reserve_model_call(
        db, run, "worker-one", fence, model, "model-1", "Fix"
    )
    db.commit()
    assert Decimal(reservation.reserved_usd) <= Decimal("0.014")
    warnings = db.scalars(select(RunEvent).where(RunEvent.event_type == "budget.warning")).all()
    assert {event.payload["scope"] for event in warnings} == {"daily", "monthly"}
