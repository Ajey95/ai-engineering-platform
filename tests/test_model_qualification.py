import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base
from platform_app.model_qualification import (
    MetadataAttestation,
    QualificationError,
    change_model_state,
    qualification_current,
    qualification_for_pinned_run,
    qualify_model_entry,
    register_model_entry,
)
from platform_app.models import (
    ModelEntry,
    ModelRegistryEvent,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    SandboxLease,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.providers import ProviderTurn
from platform_app.run_ledger import (
    assert_fence,
    expire_model_approvals,
    resume_model_approval_run,
)
from platform_app.schemas import ModelRegister
from platform_app.service import ServiceError
from platform_app.tool_broker import CompletedToolCall


class ScriptedAdapter:
    provider = "openai"

    def __init__(self, fail_step=0):
        self.calls = 0
        self.fail_step = fail_step
        self.nonce = ""

    def generate(
        self, model, instruction, prompt, tools, max_output_tokens,
        previous=None, results=None, stream=False,
    ):
        self.calls += 1
        assert model == "model-a"
        assert max_output_tokens == 256
        assert stream is (self.calls > 3)
        usage = {"input_tokens": 10, "output_tokens": 5}
        phase = (self.calls - 1) % 3
        if phase == 0:
            nonce = re.search(r"[0-9a-f]{24}", prompt).group()
            assert not self.nonce or self.nonce == nonce
            self.nonce = nonce
            return ProviderTurn(
                "openai",
                "model-a-2026",
                self.nonce,
                (),
                "completed",
                usage,
                {"request_model": model},
            )
        if phase == 1:
            assert (
                tools["qualification_echo"].input_schema["properties"]["nonce"]["const"]
                == self.nonce
            )
            calls = (CompletedToolCall("call-a", "qualification_echo", {"nonce": self.nonce}),)
            if self.fail_step == self.calls:
                calls = ()
            return ProviderTurn(
                "openai", "model-a-2026", "", calls, "completed", usage, {"request_model": model}
            )
        assert previous.calls[0].call_id == "call-a"
        assert results == {"call-a": {"status": "ok", "nonce": self.nonce}}
        text = "wrong" if self.fail_step == self.calls else self.nonce
        return ProviderTurn(
            "openai", "model-a-2026", text, (), "completed", usage, {"request_model": model}
        )


@pytest.fixture
def registry(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'registry.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            ModelEntry(
                id="entry-a",
                provider="openai",
                model_id="model-a",
                registry_revision="rev-a",
                state="registered",
                capabilities={},
                context_limit=32000,
                output_limit=1000,
                price_revision="price-a",
                price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"),
            )
        )
        db.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def attestation():
    return MetadataAttestation.from_dict(
        {
            "registry_revision": "rev-a",
            "context_limit": 32000,
            "output_limit": 1000,
            "price_revision": "price-a",
            "price_per_m_input": "1",
            "price_per_m_output": "2",
            "limits_source_url": "https://vendor.example/models/model-a",
            "pricing_source_url": "https://vendor.example/pricing",
            "effective_date": "2026-10-02",
        }
    )


def test_successful_live_probe_enables_only_attested_revision(registry, attestation, monkeypatch):
    adapter = ScriptedAdapter()
    result = qualify_model_entry(registry, "entry-a", adapter, attestation, "operator-a", True)
    assert adapter.calls == 6
    assert result["state"] == "enabled"
    assert result["resolved_model"] == "model-a-2026"
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert qualification_current(model)
        assert [item.outcome for item in db.query(ModelRegistryEvent).all()] == [
            "validating",
            "enabled",
        ]
        assert model.capabilities["qualification"]["checks"] == [
            "text",
            "schema_validated_tool",
            "continuation",
            "streamed_text",
            "streamed_tool",
            "streamed_continuation",
            "usage",
        ]
        model.price_revision = "price-b"
        db.commit()
        assert not qualification_current(model)
        model.price_revision = "price-a"
        db.commit()
        monkeypatch.setattr("platform_app.model_qualification.adapter_digest", lambda: "changed")
        assert not qualification_current(model)


def test_failed_probe_disables_entry_and_keeps_no_live_marker(registry, attestation):
    with pytest.raises(QualificationError) as error:
        qualify_model_entry(
            registry, "entry-a", ScriptedAdapter(fail_step=2), attestation, "operator-a", True
        )
    assert error.value.code == "MODEL_QUALIFICATION_FAILED"
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert model.state == "registered"
        assert model.validated_at is None
        assert model.capabilities["live_qualified"] is False
        assert model.capabilities["qualification"]["status"] == "failed"
        assert [item.outcome for item in db.query(ModelRegistryEvent).all()] == [
            "validating",
            "failed",
        ]


def test_streaming_probe_failure_cannot_enable_model(registry, attestation):
    with pytest.raises(QualificationError) as error:
        qualify_model_entry(
            registry, "entry-a", ScriptedAdapter(fail_step=5), attestation,
            "operator-a", True,
        )
    assert error.value.code == "MODEL_QUALIFICATION_FAILED"
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert model.state == "registered"
        assert model.capabilities["live_qualified"] is False


def test_model_lifecycle_preserves_pinned_runs_until_emergency_disable(registry, attestation):
    qualify_model_entry(registry, "entry-a", ScriptedAdapter(), attestation, "operator-a")
    enabled = change_model_state(
        registry, "entry-a", "enable", "operator-a", "Approved for this tenant pilot",
    )
    assert enabled["state"] == "enabled"
    with registry() as db:
        assert qualification_current(db.get(ModelEntry, "entry-a"))
    change_model_state(
        registry, "entry-a", "deprecate", "operator-a",
        "New runs must use the replacement snapshot",
    )
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert not qualification_current(model)
        assert qualification_for_pinned_run(model)
    change_model_state(
        registry, "entry-a", "disable", "operator-a",
        "Emergency provider security restriction",
    )
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert not qualification_for_pinned_run(model)
        assert model.capabilities["live_qualified"] is False
        reasons = [event.reason for event in db.query(ModelRegistryEvent).all()]
        assert reasons[-3:] == [
            "Approved for this tenant pilot",
            "New runs must use the replacement snapshot",
            "Emergency provider security restriction",
        ]
    with pytest.raises(QualificationError) as error:
        change_model_state(
            registry, "entry-a", "enable", "operator-a",
            "Try enabling without requalification",
        )
    assert error.value.code == "MODEL_QUALIFICATION_REQUIRED"


def test_model_lifecycle_rejects_reasonless_transition(registry, attestation):
    qualify_model_entry(registry, "entry-a", ScriptedAdapter(), attestation, "operator-a")
    with pytest.raises(QualificationError, match="reason"):
        change_model_state(registry, "entry-a", "enable", "operator-a", " ")
    with registry() as db:
        assert db.get(ModelEntry, "entry-a").state == "qualified"


def test_emergency_disable_fences_runs_and_queues_guest_cleanup(registry, attestation):
    qualify_model_entry(registry, "entry-a", ScriptedAdapter(), attestation,
                        "operator-a", enable=True)
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        snapshot = {
            "policy_version": "1.0",
            "model_registry_revision": model.registry_revision,
            "model_price_revision": model.price_revision,
            "model_context_limit": model.context_limit,
            "model_output_limit": model.output_limit,
            "model_price_per_m_input": str(model.price_per_m_input),
            "model_price_per_m_output": str(model.price_per_m_output),
        }
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="Bug", expected_behavior="Works", actual_behavior="Fails",
            created_by="alice",
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="key-a",
            request_hash="a" * 64, base_commit="b" * 40,
            model_entry_id="entry-a", state="INVESTIGATING", verdict="NOT_RUN",
            lease_owner="worker-a", lease_fence=4,
            lease_until=datetime.now(UTC) + timedelta(minutes=2), config_snapshot=snapshot,
        ))
        db.add(SandboxLease(
            id="lease-a", tenant_id="tenant-a", project_id="project-a",
            run_id="run-a", generation=1, phase="baseline", lease_fence=4,
            client_token="c" * 64, state="provisioned", image_id="ami-12345678",
            instance_type="m6i.large", subnet_id="subnet-12345678",
            security_group_id="sg-12345678", root_device_name="/dev/xvda",
            disk_gib=40, expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ))
        db.commit()
    result = change_model_state(
        registry, "entry-a", "disable", "operator-a",
        "Emergency credential exposure containment",
    )
    assert result["affected_runs"] == 1
    with registry() as db:
        run = db.get(Run, "run-a")
        assert run.state == "PAUSED_APPROVAL"
        assert run.resume_target == "INVESTIGATING"
        assert run.lease_fence == 5
        assert run.lease_owner is None
        with pytest.raises(ServiceError) as fenced:
            assert_fence(run, "worker-a", 4)
        assert fenced.value.code == "LEASE_LOST"
        assert db.get(SandboxLease, "lease-a").state == "revoked"
        assert db.query(OutboxEvent).filter_by(topic="sandbox.cleanup").count() == 1
        assert [event.event_type for event in db.query(RunEvent).order_by(RunEvent.sequence)] == [
            "run.state_changed", "approval.required", "sandbox.revoked",
        ]
        with pytest.raises(ServiceError) as not_qualified:
            resume_model_approval_run(db, "tenant-a", "run-a", "owner-a",
                                      "Reviewed provider recovery", "approval-key-a")
        assert not_qualified.value.code == "MODEL_QUALIFICATION_REQUIRED"
        db.rollback()
        required = db.query(RunEvent).filter_by(event_type="approval.required").one()
        original_time = required.created_at
        required.created_at = datetime.now(UTC) - timedelta(hours=25)
        db.commit()
        with pytest.raises(ServiceError) as expired:
            resume_model_approval_run(db, "tenant-a", "run-a", "owner-a",
                                      "Reviewed provider recovery", "approval-key-a")
        assert expired.value.code == "APPROVAL_EXPIRED"
        db.rollback()
        required = db.query(RunEvent).filter_by(event_type="approval.required").one()
        required.created_at = original_time
        db.commit()
    qualify_model_entry(registry, "entry-a", ScriptedAdapter(), attestation,
                        "operator-a", enable=True)
    with registry() as db:
        db.add(ToolAction(
            tenant_id="tenant-a", run_id="run-a", step_id="model-a",
            logical_action="model.generate", effect_key="e" * 64,
            arguments_hash="a" * 64, policy_result="allowed", status="INTENDED",
        ))
        db.commit()
        with pytest.raises(ServiceError) as uncertain:
            resume_model_approval_run(db, "tenant-a", "run-a", "owner-a",
                                      "Reviewed provider recovery", "approval-key-a")
        assert uncertain.value.code == "EFFECT_OUTCOME_UNKNOWN"
        db.rollback()
        db.query(ToolAction).one().status = "COMPLETED"
        db.get(Tenant, "tenant-a").policy_revision = "2.0"
        db.commit()
        with pytest.raises(ServiceError) as policy:
            resume_model_approval_run(db, "tenant-a", "run-a", "owner-a",
                                      "Reviewed provider recovery", "approval-key-a")
        assert policy.value.code == "POLICY_REVIEW_REQUIRED"
        db.rollback()
        db.get(Tenant, "tenant-a").policy_revision = "1.0"
        db.commit()
        resumed = resume_model_approval_run(
            db, "tenant-a", "run-a", "owner-a", "Reviewed provider recovery",
            "approval-key-a",
        )
        db.commit()
        assert resumed.state == "QUEUED"
        assert resume_model_approval_run(
            db, "tenant-a", "run-a", "owner-a", "Reviewed provider recovery",
            "approval-key-a",
        ).id == resumed.id
        assert db.query(OutboxEvent).filter_by(topic="run.dispatch", status="pending").count() == 1
        assert db.query(RunEvent).filter_by(event_type="approval.granted").count() == 1
        db.add(Run(
            id="run-expired", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="key-expired",
            request_hash="c" * 64, base_commit="b" * 40,
            model_entry_id="entry-a", state="PAUSED_APPROVAL",
            resume_target="PATCHING", config_snapshot=snapshot, last_sequence=1,
        ))
        db.add(RunEvent(
            tenant_id="tenant-a", run_id="run-expired", sequence=1,
            event_type="approval.required", payload={"kind": "model_emergency_disable"},
            created_at=datetime.now(UTC) - timedelta(hours=25),
        ))
        db.commit()
        assert expire_model_approvals(db, "tenant-a") == 1
        db.commit()
        assert db.get(Run, "run-expired").state == "CANCELLED"
        assert expire_model_approvals(db, "tenant-a") == 0
        assert db.query(RunEvent).filter_by(
            run_id="run-expired", event_type="approval.expired"
        ).count() == 1


def test_mismatched_metadata_is_rejected_before_spend(registry, attestation):
    changed = MetadataAttestation.from_dict(
        {
            **attestation.__dict__,
            "context_limit": 64000,
            "price_per_m_input": "1",
            "price_per_m_output": "2",
        }
    )
    adapter = ScriptedAdapter()
    with pytest.raises(QualificationError) as error:
        qualify_model_entry(registry, "entry-a", adapter, changed, "operator-a", True)
    assert error.value.code == "MODEL_REVISION_CHANGED"
    assert adapter.calls == 0


def test_registration_keeps_declared_capabilities_untrusted(registry):
    with registry() as db:
        model = register_model_entry(
            db,
            ModelRegister(
                id="entry-b",
                provider="google",
                model_id="model-b",
                registry_revision="rev-b",
                context_limit=32000,
                output_limit=1000,
                price_revision="price-b",
                price_per_m_input=1,
                price_per_m_output=2,
                capabilities={"live_qualified": True, "database_fixture_only": True},
            ),
            "operator-a",
        )
        db.commit()
        assert model.state == "registered"
        assert model.capabilities["declared"]["live_qualified"] is True
        assert model.capabilities.get("live_qualified") is None
        event = db.query(ModelRegistryEvent).one()
        assert event.action == "register"
        assert event.outcome == "registered"
