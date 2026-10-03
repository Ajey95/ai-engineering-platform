import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import agent_patch
from platform_app.agent_patch import request_fixture_patch
from platform_app.db import Base
from platform_app.model_budget import reject_model_call, reserve_model_call, settle_model_call
from platform_app.model_qualification import adapter_digest
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
from platform_app.providers import OpenAIResponses, ProviderError, ProviderTurn
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


def test_reservation_refreshes_stale_model_after_emergency_disable(scope):
    db, run, model, fence = scope
    db.execute(update(ModelEntry).where(ModelEntry.id == model.id).values(
        state="disabled"
    ).execution_options(synchronize_session=False))
    assert model.state == "enabled"
    with pytest.raises(ServiceError) as error:
        reserve_model_call(db, run, "worker-one", fence, model, "model-1", "Fix the form")
    assert error.value.code == "MODEL_UNAVAILABLE"
    assert db.scalar(select(BudgetEntry.id)) is None


def test_definitive_rejection_releases_reservation_but_unknown_outcome_stays_pending(scope):
    db, run, model, fence = scope
    action, reservation, _ = reserve_model_call(
        db, run, "worker-one", fence, model, "model-1", "Fix the form"
    )
    db.commit()
    with pytest.raises(ServiceError) as uncertain:
        reject_model_call(
            db, run, "worker-one", fence, action, reservation,
            "PROVIDER_TIMEOUT", 504,
        )
    assert uncertain.value.code == "EFFECT_OUTCOME_UNKNOWN"
    assert action.status == "INTENDED" and reservation.status == "reserved"
    reject_model_call(
        db, run, "worker-one", fence, action, reservation,
        "PROVIDER_RATE_LIMITED", 429,
    )
    db.commit()
    assert action.status == "COMPLETED"
    assert action.receipt == {
        "status": "REJECTED", "error_code": "PROVIDER_RATE_LIMITED", "http_status": 429,
    }
    assert reservation.status == "released" and Decimal(reservation.actual_usd) == 0
    assert db.scalar(select(RunEvent).where(RunEvent.event_type == "model.rejected"))
    with pytest.raises(ServiceError) as no_retry:
        reserve_model_call(db, run, "worker-one", fence, model, "model-2", "Again")
    assert no_retry.value.code == "BUDGET_EXHAUSTED"


def test_fixture_request_records_http_rejection_and_does_not_repeat_it(scope, tmp_path):
    db, run, _, fence = scope
    run.config_snapshot = {**run.config_snapshot, "max_model_calls": 2}
    calls = []

    def reject(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    adapter = OpenAIResponses(
        "test-only-key", client=httpx.Client(transport=httpx.MockTransport(reject))
    )
    source = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/form-submit/base/server.py"
    db.commit()
    with pytest.raises(ProviderError) as rejected:
        request_fixture_patch(
            lambda: Session(db.bind), run.id, "worker-one", fence,
            {}, source.read_text(encoding="utf-8"), tmp_path, provider=adapter,
        )
    assert rejected.value.code == "PROVIDER_RATE_LIMITED"
    with Session(db.bind) as check:
        action = check.scalar(select(ToolAction).where(
            ToolAction.logical_action == "model.generate"
        ))
        reservation = check.scalar(select(BudgetEntry).where(
            BudgetEntry.category == "call:model-1"
        ))
        assert action.receipt["status"] == "REJECTED"
        assert reservation.status == "released"
    with pytest.raises(ServiceError) as replay:
        request_fixture_patch(
            lambda: Session(db.bind), run.id, "worker-one", fence,
            {}, source.read_text(encoding="utf-8"), tmp_path, provider=adapter,
        )
    assert replay.value.code == "PROVIDER_RATE_LIMITED"
    assert len(calls) == 1


def test_fixture_timeout_remains_uncertain_and_cannot_reissue(scope, tmp_path):
    db, run, _, fence = scope
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown provider outcome")

    adapter = OpenAIResponses(
        "test-only-key", client=httpx.Client(transport=httpx.MockTransport(timeout))
    )
    source = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/form-submit/base/server.py"
    db.commit()
    for expected in ("PROVIDER_TIMEOUT", "EFFECT_OUTCOME_UNKNOWN"):
        with pytest.raises((ProviderError, ServiceError)) as error:
            request_fixture_patch(
                lambda: Session(db.bind), run.id, "worker-one", fence,
                {}, source.read_text(encoding="utf-8"), tmp_path, provider=adapter,
            )
        assert error.value.code == expected
    with Session(db.bind) as check:
        action = check.scalar(select(ToolAction).where(
            ToolAction.logical_action == "model.generate"
        ))
        reservation = check.scalar(select(BudgetEntry).where(
            BudgetEntry.category == "call:model-1"
        ))
        assert action.status == "INTENDED"
        assert reservation.status == "reserved"
        unknown = check.scalar(select(RunEvent).where(
            RunEvent.run_id == run.id, RunEvent.event_type == "model.uncertain"
        ))
        assert unknown.payload["error_code"] == "PROVIDER_TIMEOUT"
        assert unknown.payload["liability_status"] == "reserved"
    assert len(calls) == 1


def test_rate_limit_switches_once_only_to_admission_and_current_authorized_provider(
    scope, tmp_path, monkeypatch,
):
    db, run, _, fence = scope
    route = {
        "from_model_entry_id": "model-a", "to_model_entry_id": "model-b",
        "data_classes": ["source_code"],
    }
    db.get(Tenant, "tenant-a").model_routing_policy = {
        "enabled": False, "failover_routes": [route],
    }
    run.config_snapshot = {
        **run.config_snapshot, "max_model_calls": 2, "model_failover_routes": [route],
    }
    db.add(ModelEntry(
        id="model-b", provider="anthropic", model_id="anthropic-model",
        registry_revision="revision-b", state="enabled", context_limit=32000,
        output_limit=4000, price_revision="price-b",
        price_per_m_input=Decimal("1"), price_per_m_output=Decimal("2"),
        validated_at=datetime.now(UTC), capabilities={
            "live_qualified": True,
            "qualification": {
                "status": "passed", "provider": "anthropic",
                "requested_model": "anthropic-model", "registry_revision": "revision-b",
                "context_limit": 32000, "output_limit": 4000,
                "price_revision": "price-b", "price_per_m_input": "1.000000",
                "price_per_m_output": "2.000000", "adapter_digest": adapter_digest(),
                "checks": ["text", "schema_validated_tool", "continuation", "usage"],
            },
        },
    ))
    db.commit()
    source_calls = []

    def rate_limit(request):
        source_calls.append(request)
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    source_adapter = OpenAIResponses(
        "test-only-key", client=httpx.Client(transport=httpx.MockTransport(rate_limit))
    )
    source = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/form-submit/base/server.py"
    fixed = source.read_text(encoding="utf-8").replace(
        "quantity: int | None = None", "quantity: int = 1"
    )
    target_calls = []

    class Target:
        def generate(self, model, instruction, prompt, tools, max_output_tokens):
            target_calls.append((model, prompt))
            return ProviderTurn(
                "anthropic", model, json.dumps({
                    "diagnosis": "Missing default quantity",
                    "files": [{"path": "server.py", "content": fixed}],
                }), (), "end_turn", {"input_tokens": 100, "output_tokens": 100}, {},
            )

    monkeypatch.setattr(agent_patch, "_provider", lambda model: Target())
    def factory():
        return Session(db.bind)
    result = request_fixture_patch(
        factory, run.id, "worker-one", fence, {},
        source.read_text(encoding="utf-8"), tmp_path, provider=source_adapter,
    )
    assert result.proposal.files[0][0] == "server.py"
    with Session(db.bind) as check:
        saved_run = check.get(Run, run.id)
        actions = check.scalars(select(ToolAction).where(
            ToolAction.logical_action == "model.generate"
        ).order_by(ToolAction.step_id)).all()
        reservations = check.scalars(select(BudgetEntry).where(
            BudgetEntry.category.like("call:%")
        ).order_by(BudgetEntry.category)).all()
        assert saved_run.model_entry_id == "model-b"
        assert saved_run.config_snapshot["model_failover"]["reason"] == "PROVIDER_RATE_LIMITED"
        assert [action.receipt["status"] for action in actions] == ["REJECTED", "COMPLETED"]
        assert [item.status for item in reservations] == ["released", "settled"]
        assert check.scalar(select(RunEvent).where(RunEvent.event_type == "model.failover"))
    replay = request_fixture_patch(
        factory, run.id, "worker-one", fence, {},
        source.read_text(encoding="utf-8"), tmp_path, provider=source_adapter,
    )
    assert replay == result
    assert len(source_calls) == len(target_calls) == 1


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


def test_provider_overrun_records_tenant_cap_breach_and_blocks_next_reservation(scope):
    db, run, model, fence = scope
    tenant = db.get(Tenant, "tenant-a")
    tenant.daily_inference_cap_usd = Decimal("0.014")
    tenant.monthly_inference_cap_usd = Decimal("0.014")
    run.config_snapshot = {**run.config_snapshot, "max_model_calls": 2}
    action, reservation, _ = reserve_model_call(
        db, run, "worker-one", fence, model, "model-1", "Fix"
    )
    db.commit()
    settle_model_call(
        db, run, "worker-one", fence, action, reservation, model,
        {"input_tokens": 20_000, "output_tokens": 10},
        hashlib.sha256(b"output").hexdigest(), "private/model-1.json",
    )
    db.commit()
    breaches = db.scalars(select(RunEvent).where(RunEvent.event_type == "budget.breached")).all()
    assert reservation.status == "overrun"
    assert {event.payload["scope"] for event in breaches} == {"daily", "monthly"}
    with pytest.raises(ServiceError) as error:
        reserve_model_call(db, run, "worker-one", fence, model, "model-2", "Again")
    assert error.value.code == "TENANT_BUDGET_EXHAUSTED"


def test_disabled_tenant_can_still_settle_existing_provider_liability(scope):
    db, run, model, fence = scope
    action, reservation, _ = reserve_model_call(
        db, run, "worker-one", fence, model, "model-1", "Fix"
    )
    db.commit()
    db.get(Tenant, "tenant-a").status = "disabled"
    db.commit()
    actual = settle_model_call(
        db, run, "worker-one", fence, action, reservation, model,
        {"input_tokens": 10, "output_tokens": 20},
        hashlib.sha256(b"output").hexdigest(), "private/model-1.json",
    )
    db.commit()
    assert actual > 0
    assert reservation.status == "settled"
