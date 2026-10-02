import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import Run, RunEvent, Tenant
from platform_app.run_ledger import (
    begin_tool_action,
    claim_run,
    complete_tool_action,
    transition,
)
from platform_app.service import ServiceError


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="A"))
        run = Run(
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
            config_snapshot={"policy_version": "1.0"},
        )
        session.add(run)
        # This fixture tests lease behavior; SQLite does not enforce these foreign keys.
        session.commit()
        yield session
    engine.dispose()


def test_fence_blocks_stale_worker_and_effect_replay(db):
    run, fence = claim_run(db, "run-a", "worker-one")
    db.commit()
    transition(db, run, "worker-one", fence, "PREPARING")
    db.commit()
    action = begin_tool_action(
        db, run, "worker-one", fence, "step-1", "named-test", {"target_name": "unit"}, True
    )
    db.commit()
    with pytest.raises(ServiceError) as unknown:
        begin_tool_action(
            db, run, "worker-one", fence, "step-1", "named-test", {"target_name": "unit"}, True
        )
    assert unknown.value.code == "EFFECT_OUTCOME_UNKNOWN"
    complete_tool_action(db, run, "worker-one", fence, action, {"status": "failed", "exit_code": 1})
    db.commit()
    repeat = begin_tool_action(
        db, run, "worker-one", fence, "step-1", "named-test", {"target_name": "unit"}, True
    )
    assert repeat.id == action.id
    with pytest.raises(ServiceError) as stale:
        transition(db, run, "old-worker", fence, "REPRODUCING")
    assert stale.value.code == "LEASE_LOST"


def test_invalid_state_jump_is_rejected(db):
    run, fence = claim_run(db, "run-a", "worker-one")
    with pytest.raises(ServiceError) as error:
        transition(db, run, "worker-one", fence, "REVIEW_READY", "PASSED")
    assert error.value.code == "INVALID_TRANSITION"


def test_pause_releases_lease_and_cannot_be_claimed(db):
    run, fence = claim_run(db, "run-a", "worker-one")
    transition(db, run, "worker-one", fence, "PREPARING")
    transition(db, run, "worker-one", fence, "PAUSED_INPUT")
    db.commit()
    assert run.lease_owner is None
    assert run.lease_until is None
    with pytest.raises(ServiceError) as error:
        claim_run(db, "run-a", "worker-two")
    assert error.value.code == "RUN_CLOSED"


def test_three_identical_completed_actions_without_progress_fail_run(db):
    run, fence = claim_run(db, "run-a", "worker-one")
    transition(db, run, "worker-one", fence, "PREPARING")
    transition(db, run, "worker-one", fence, "REPRODUCING")
    for number in range(3):
        action = begin_tool_action(
            db, run, "worker-one", fence, f"retry-{number}",
            "browser.inspect", {"path": "/items"}, True,
        )
        complete_tool_action(
            db, run, "worker-one", fence, action,
            {"status": "FAILED", "exit_code": 1, "output_sha256": "a" * 64,
             "duration_ms": number + 1},
        )
        db.commit()
    assert run.state == "FAILED" and run.verdict == "INCONCLUSIVE"
    assert run.lease_owner is None
    events = db.query(RunEvent).filter_by(run_id=run.id, event_type="run.loop_detected").all()
    assert len(events) == 1
    assert events[0].payload["repetitions"] == 3


def test_changed_result_resets_repeated_action_guard(db):
    run, fence = claim_run(db, "run-a", "worker-one")
    transition(db, run, "worker-one", fence, "PREPARING")
    for number, digest in enumerate(("a", "b", "b")):
        action = begin_tool_action(
            db, run, "worker-one", fence, f"retry-{number}",
            "source.read", {"path": "server.py"}, True,
        )
        complete_tool_action(
            db, run, "worker-one", fence, action,
            {"status": "PASSED", "output_sha256": digest * 64},
        )
        db.commit()
    assert run.state == "PREPARING"
    assert db.query(RunEvent).filter_by(run_id=run.id, event_type="run.loop_detected").count() == 0
