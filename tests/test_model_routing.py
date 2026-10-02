from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.model_qualification import adapter_digest
from platform_app.model_routing import (
    RoutingError,
    select_qualified_model,
    validate_failover_routes,
    validate_routing_policy,
)
from platform_app.models import (
    ModelEntry,
    ModelRoutingEvidence,
    Project,
    RunEvent,
    Task,
    Tenant,
)
from platform_app.schemas import RunCreate
from platform_app.service import ServiceError, admit_run


def qualified_model(model_id: str, provider: str) -> ModelEntry:
    return ModelEntry(
        id=model_id, provider=provider, model_id=f"{provider}-model",
        registry_revision="revision-1", state="enabled", context_limit=32000,
        output_limit=4000, price_revision="price-1",
        price_per_m_input=Decimal("1.000000"),
        price_per_m_output=Decimal("2.000000"), validated_at=datetime.now(UTC),
        capabilities={
            "live_qualified": True,
            "qualification": {
                "status": "passed", "provider": provider,
                "requested_model": f"{provider}-model", "registry_revision": "revision-1",
                "context_limit": 32000, "output_limit": 4000,
                "price_revision": "price-1", "price_per_m_input": "1.000000",
                "price_per_m_output": "2.000000", "adapter_digest": adapter_digest(),
                "checks": ["text", "schema_validated_tool", "continuation", "usage"],
            },
        },
    )


def evidence(model_id: str, utility: str, latency: int, cost: str, **changes):
    values = {
        "model_entry_id": model_id, "registry_revision": "revision-1",
        "task_class": "repair", "suite_revision": "platform-repair-v1",
        "sample_count": 30, "success_rate": Decimal(utility),
        "p95_latency_ms": latency, "mean_cost_usd": Decimal(cost),
        "available": True, "source_sha256": "a" * 64,
        "observed_at": datetime.now(UTC),
    }
    values.update(changes)
    return ModelRoutingEvidence(**values)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(
            id="tenant-a", name="A",
            model_routing_policy={
                "enabled": True,
                "allowed_model_entry_ids": ["model-a", "model-b"],
                "allowed_data_classes": ["source_code"],
                "weights": {"utility": 1, "latency": "0.02", "cost": "0.2"},
            },
        ))
        session.add(Project(
            id="project-a", tenant_id="tenant-a", name="Fixture",
            repository_url="https://example.test/repo.git", test_url="http://fixture.test",
            environment_manifest={"case_id": "form-submit-001", "data_class": "source_code"},
        ))
        session.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Submitting a valid form returns 500", expected_behavior="Created",
            actual_behavior="500", created_by="alice",
        ))
        session.add_all([
            qualified_model("model-a", "openai"),
            qualified_model("model-b", "anthropic"),
        ])
        session.flush()
        session.add_all([
            evidence("model-a", "0.9500", 10000, "1.000000"),
            evidence("model-b", "0.9000", 1000, "0.200000"),
        ])
        session.commit()
        yield session
    engine.dispose()


def test_auto_route_filters_then_scores_and_pins_evidence(db):
    tenant = db.get(Tenant, "tenant-a")
    project = db.get(Project, "project-a")
    route = {
        "from_model_entry_id": "model-b", "to_model_entry_id": "model-a",
        "data_classes": ["source_code"],
    }
    tenant.model_routing_policy = {
        **tenant.model_routing_policy, "failover_routes": [route],
    }
    decision = select_qualified_model(db, tenant, project)
    assert decision.model.id == "model-b"
    run = admit_run(
        db, "tenant-a", "alice", "task-a", "auto-key",
        RunCreate(
            base_commit="a" * 40, selected_model_entry="auto",
            reproduction={"fixture_case_id": "form-submit-001"},
        ),
    )
    db.commit()
    assert run.model_entry_id == "model-b"
    assert run.config_snapshot["model_route"]["evidence_id"] == decision.evidence.id
    assert run.config_snapshot["model_failover_routes"] == [route]
    assert db.scalar(select(RunEvent).where(RunEvent.event_type == "model.routed"))


def test_auto_route_denies_unapproved_data_and_missing_evidence(db):
    tenant = db.get(Tenant, "tenant-a")
    project = db.get(Project, "project-a")
    project.environment_manifest = {"data_class": "restricted_source"}
    with pytest.raises(RoutingError) as denied:
        select_qualified_model(db, tenant, project)
    assert denied.value.code == "MODEL_DATA_POLICY_DENIED"
    project.environment_manifest = {"data_class": "source_code"}
    for item in db.scalars(select(ModelRoutingEvidence)):
        item.available = False
    db.flush()
    with pytest.raises(ServiceError) as missing:
        admit_run(
            db, "tenant-a", "alice", "task-a", "unavailable-key",
            RunCreate(base_commit="a" * 40, selected_model_entry="auto"),
        )
    assert missing.value.code == "MODEL_ROUTE_UNAVAILABLE"


def test_auto_route_rejects_stale_qualification_capacity_and_metrics(db):
    tenant = db.get(Tenant, "tenant-a")
    project = db.get(Project, "project-a")
    db.get(ModelEntry, "model-b").registry_revision = "changed"
    assert select_qualified_model(db, tenant, project).model.id == "model-a"
    db.get(ModelEntry, "model-a").context_limit = 4096
    with pytest.raises(RoutingError, match="current evidence"):
        select_qualified_model(db, tenant, project)
    db.get(ModelEntry, "model-a").context_limit = 32000
    only = db.scalar(select(ModelRoutingEvidence).where(
        ModelRoutingEvidence.model_entry_id == "model-a"
    ))
    only.observed_at = datetime.now(UTC) - timedelta(days=31)
    with pytest.raises(RoutingError, match="current evidence"):
        select_qualified_model(db, tenant, project)


def test_routing_policy_rejects_unreviewed_fields_and_invalid_weights():
    policy = {
        "enabled": True, "allowed_model_entry_ids": ["model-a"],
        "allowed_data_classes": ["source_code"],
        "weights": {"utility": 1, "latency": 0, "cost": 0},
    }
    with pytest.raises(RoutingError) as extra:
        validate_routing_policy({**policy, "allow_any_provider": True})
    assert extra.value.code == "MODEL_ROUTING_POLICY_INVALID"
    with pytest.raises(RoutingError) as invalid:
        validate_routing_policy({**policy, "weights": {
            "utility": "NaN", "latency": 0, "cost": 0,
        }})
    assert invalid.value.code == "MODEL_ROUTING_POLICY_INVALID"


def test_failover_policy_requires_one_exact_alternate_per_source_and_data_class():
    route = {
        "from_model_entry_id": "model-a", "to_model_entry_id": "model-b",
        "data_classes": ["source_code"],
    }
    assert validate_failover_routes({"enabled": False, "failover_routes": [route]}) == [route]
    for routes in (
        [route, route],
        [{**route, "to_model_entry_id": "model-a"}],
        [{**route, "unreviewed": True}],
    ):
        with pytest.raises(RoutingError) as invalid:
            validate_failover_routes({"enabled": False, "failover_routes": routes})
        assert invalid.value.code == "MODEL_ROUTING_POLICY_INVALID"
