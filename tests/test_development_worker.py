import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base
from platform_app.development_worker import DevelopmentWorker, _pinned_fixture, _verified_receipt
from platform_app.model_base import utcnow
from platform_app.models import OutboxEvent, Run, RunEvent, Tenant, ToolAction
from platform_app.service import ServiceError


def test_pinned_fixture_contains_baseline_and_oracle(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    import subprocess

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    manifest, workspace, oracle = _pinned_fixture(repository, commit, tmp_path)
    assert manifest.is_file()
    assert (workspace / "server.py").is_file()
    assert oracle.is_file()
    assert not oracle.is_relative_to(workspace)


def test_untrusted_fixture_revision_is_rejected(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    with pytest.raises(ServiceError) as error:
        _pinned_fixture(repository, "0" * 40, tmp_path)
    assert error.value.code == "FIXTURE_UNAVAILABLE"


def test_completed_effect_needs_its_matching_artifacts(tmp_path):
    receipt = {"status": "PASSED", "output_file": "test-baseline.log", "output_sha256": "0" * 64}
    (tmp_path / "test-baseline.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ServiceError) as error:
        _verified_receipt("named", tmp_path, receipt)
    assert error.value.code == "EFFECT_OUTCOME_UNKNOWN"


def test_development_worker_does_not_consume_other_dispatches(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
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
                config_snapshot={"reproduction": {}},
            )
        )
        db.add(
            OutboxEvent(
                id="event-a",
                tenant_id="tenant-a",
                topic="run.dispatch",
                payload={"run_id": "run-a"},
            )
        )
        db.commit()
    worker = DevelopmentWorker(
        Path(__file__).resolve().parents[1], tmp_path, session_factory=factory
    )
    assert worker.process_next() is None
    with factory() as db:
        assert db.scalar(select(OutboxEvent.status)) == "pending"
    engine.dispose()


def test_recovery_blocks_uncertain_effect_replay(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
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
                state="REPRODUCING",
                lease_owner="crashed-worker",
                lease_fence=1,
                lease_until=utcnow() - timedelta(minutes=2),
                config_snapshot={
                    "policy_version": "1.0",
                    "reproduction": {"fixture_case_id": "form-submit-001"},
                },
            )
        )
        db.add(
            OutboxEvent(
                id="event-a",
                tenant_id="tenant-a",
                topic="run.dispatch",
                payload={"run_id": "run-a"},
                status="processing",
            )
        )
        db.add(
            ToolAction(
                tenant_id="tenant-a",
                run_id="run-a",
                step_id="browser",
                logical_action="fixture.browser",
                effect_key="effect-a",
                arguments_hash="hash",
                policy_result="allowed",
                status="INTENDED",
            )
        )
        db.commit()
    worker = DevelopmentWorker(
        Path(__file__).resolve().parents[1], tmp_path, session_factory=factory
    )
    assert worker.recover_stale() == 1
    with factory() as db:
        assert db.get(OutboxEvent, "event-a").status == "failed"
        assert db.get(Run, "run-a").state == "INCONCLUSIVE"
        assert db.scalar(select(ToolAction.status)) == "INTENDED"
        error = db.scalar(select(RunEvent).where(RunEvent.event_type == "worker.error"))
        assert error.payload["code"] == "EFFECT_OUTCOME_UNKNOWN"
    engine.dispose()


def test_recovery_acknowledges_review_ready_after_worker_crash(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'review-recovery.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(
            Run(
                id="run-a", tenant_id="tenant-a", task_id="task-a", project_id="project-a",
                created_by="alice", idempotency_key="key-a", request_hash="hash",
                base_commit="a" * 40, model_entry_id="model-a", state="REVIEW_READY",
                lease_owner="crashed-worker", lease_fence=1,
                lease_until=utcnow() - timedelta(minutes=2), config_snapshot={},
            )
        )
        db.add(
            OutboxEvent(
                id="event-a", tenant_id="tenant-a", topic="run.dispatch",
                payload={"run_id": "run-a"}, status="processing",
            )
        )
        db.commit()
    worker = DevelopmentWorker(
        Path(__file__).resolve().parents[1], tmp_path, session_factory=factory
    )
    assert worker.recover_stale() == 1
    with factory() as db:
        assert db.get(Run, "run-a").state == "REVIEW_READY"
        assert db.get(OutboxEvent, "event-a").status == "delivered"
    engine.dispose()


def test_duplicate_dispatch_for_closed_run_is_acknowledged(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'duplicate.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
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
                state="INCONCLUSIVE",
                config_snapshot={"reproduction": {"fixture_case_id": "form-submit-001"}},
            )
        )
        db.add(
            OutboxEvent(
                id="event-a",
                tenant_id="tenant-a",
                topic="run.dispatch",
                payload={"run_id": "run-a"},
            )
        )
        db.commit()
    worker = DevelopmentWorker(
        Path(__file__).resolve().parents[1], tmp_path, session_factory=factory
    )
    assert worker.process_next() is None
    with factory() as db:
        assert db.get(OutboxEvent, "event-a").status == "delivered"
    engine.dispose()


def test_spend_exhaustion_pauses_and_acks_dispatch(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'budget-pause.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", task_id="task-a", project_id="project-a",
            created_by="alice", idempotency_key="key-a", request_hash="hash",
            base_commit="a" * 40, model_entry_id="model-a", state="QUEUED",
            config_snapshot={
                "reproduction": {"fixture_case_id": "form-submit-001"},
                "spend_limit_usd": "1.000000",
            },
        ))
        db.add(OutboxEvent(
            id="event-a", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-a"},
        ))
        db.commit()
    worker = DevelopmentWorker(
        Path(__file__).resolve().parents[1], tmp_path, session_factory=factory
    )

    def exhausted(run_id, fence, _commit):
        worker._transition(run_id, fence, "REPRODUCING")
        worker._transition(run_id, fence, "INVESTIGATING")
        raise ServiceError("RUN_SPEND_EXHAUSTED", "Run spend limit reached", 409)

    monkeypatch.setattr(worker, "_run_workflow", exhausted)
    assert worker.process_next() == "run-a"
    with factory() as db:
        assert db.get(Run, "run-a").state == "PAUSED_BUDGET"
        assert db.get(Run, "run-a").resume_target == "INVESTIGATING"
        assert db.get(Run, "run-a").lease_owner is None
        assert db.get(OutboxEvent, "event-a").status == "delivered"
        assert db.scalar(select(RunEvent).where(RunEvent.event_type == "budget.pause"))
    engine.dispose()
