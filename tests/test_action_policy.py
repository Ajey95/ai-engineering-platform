from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.action_policy import FIXTURE_VERSION, ActionIntent, authorize_run_effect
from platform_app.db import Base, utcnow
from platform_app.models import AuditEvent, Project, Run, SandboxLease, Task, Tenant, ToolAction
from platform_app.run_ledger import claim_run, complete_tool_action
from platform_app.service import ServiceError


@pytest.fixture
def scope(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'policy.db').as_posix()}")
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
                report="500",
                expected_behavior="201",
                actual_behavior="500",
                created_by="alice",
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
                    "max_tool_calls": 2,
                    "reproduction": {"fixture_case_id": "form-submit-001"},
                    "repository_url": "https://example.test/repo",
                    "test_url": "https://example.test",
                    "environment_manifest": manifest,
                },
            )
        )
        db.commit()
        run, fence = claim_run(db, "run-a", "worker-a")
        db.commit()
        yield db, run, fence
    engine.dispose()


def named_intent(**changes):
    fields = {
        "step_id": "named",
        "name": "fixture.named",
        "action_class": "isolated_execution",
        "target": "form-submit-001",
        "source_version": FIXTURE_VERSION,
        "arguments": {"fixture_case_id": "form-submit-001", "base_commit": "a" * 40},
    }
    fields.update(changes)
    return ActionIntent(**fields)


def test_reviewed_fixture_action_can_complete_and_replay(scope):
    db, run, fence = scope
    intent = named_intent()
    action = authorize_run_effect(db, run, "worker-a", fence, intent)
    db.commit()
    complete_tool_action(db, run, "worker-a", fence, action, {"status": "PASSED"})
    db.commit()
    replay = authorize_run_effect(db, run, "worker-a", fence, intent)
    assert replay.id == action.id
    assert replay.status == "COMPLETED"
    assert len(db.scalars(select(AuditEvent)).all()) == 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"target": "other-case"}, "action_manifest"),
        ({"source_version": "unreviewed-v2"}, "action_manifest"),
        ({"name": "plugin.publish"}, "action_unregistered"),
    ],
)
def test_unreviewed_action_is_denied_and_audited(scope, change, reason):
    db, run, fence = scope
    with pytest.raises(ServiceError) as error:
        authorize_run_effect(db, run, "worker-a", fence, named_intent(**change))
    assert error.value.code == "TOOL_DENIED"
    assert db.scalar(select(ToolAction.status)) == "DENIED"
    assert db.scalar(select(AuditEvent.outcome)) == f"denied:{reason}"


def test_policy_revision_change_fails_closed(scope):
    db, run, fence = scope
    db.get(Tenant, "tenant-a").policy_revision = "2.0"
    db.commit()
    with pytest.raises(ServiceError, match="policy_revision"):
        authorize_run_effect(db, run, "worker-a", fence, named_intent())
    assert db.scalar(select(ToolAction.status)) == "DENIED"
    db.get(Tenant, "tenant-a").policy_revision = "1.0"
    db.commit()
    with pytest.raises(ServiceError, match="Effect must be reconciled"):
        authorize_run_effect(db, run, "worker-a", fence, named_intent())


def test_tool_budget_blocks_new_effect(scope):
    db, run, fence = scope
    authorize_run_effect(db, run, "worker-a", fence, named_intent())
    db.commit()
    run.config_snapshot = {**run.config_snapshot, "max_tool_calls": 1}
    db.commit()
    with pytest.raises(ServiceError, match="tool_budget"):
        authorize_run_effect(
            db,
            run,
            "worker-a",
            fence,
            named_intent(step_id="browser", name="fixture.browser"),
        )
    assert [row.status for row in db.scalars(select(ToolAction)).all()] == ["INTENDED", "DENIED"]


def test_general_model_effect_requires_recorded_vm_baseline(scope):
    db, run, fence = scope
    run.state = "INVESTIGATING"
    run.config_snapshot = {
        **run.config_snapshot,
        "execution_profile": "hosted_vm_v1",
        "model_registry_revision": "revision-a",
        "model_price_revision": "price-a",
    }
    db.commit()

    def intent(step):
        return ActionIntent(
            step_id=step, name="model.generate", action_class="provider_request",
            target="model-a", source_version="revision-a",
            arguments={
                "model_entry_id": "model-a", "model_revision": "revision-a",
                "price_revision": "price-a",
            },
        )

    with pytest.raises(ServiceError, match="baseline_missing"):
        authorize_run_effect(db, run, "worker-a", fence, intent("general-model-1"))
    db.add(SandboxLease(
        id="lease-a", tenant_id="tenant-a", project_id="project-a",
        run_id="run-a", generation=1, phase="baseline", lease_fence=fence,
        client_token="a" * 64, state="terminated", image_id="ami-12345678",
        instance_type="m6i.large", subnet_id="subnet-12345678",
        security_group_id="sg-12345678", root_device_name="/dev/xvda",
        disk_gib=40, expires_at=utcnow() + timedelta(minutes=5),
        result_sha256="b" * 64, result_received_at=utcnow(),
        result_summary={"phase": "baseline"},
    ))
    db.commit()
    action = authorize_run_effect(db, run, "worker-a", fence, intent("general-model-2"))
    assert action.status == "INTENDED"
