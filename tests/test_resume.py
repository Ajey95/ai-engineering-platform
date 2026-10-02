import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.db import Base
from platform_app.models import OutboxEvent, Run, RunEvent, Tenant, ToolAction
from platform_app.run_ledger import claim_run, resume_input_run, transition
from platform_app.service import ServiceError


def _paused(db: Session) -> Run:
    db.add(Tenant(id="tenant", name="Fixture"))
    run = Run(
        id="run",
        tenant_id="tenant",
        project_id="project",
        task_id="task",
        created_by="actor",
        idempotency_key="admission-key",
        request_hash="r" * 64,
        base_commit="a" * 40,
        model_entry_id="model",
        state="QUEUED",
        config_snapshot={"policy_version": "1.0"},
    )
    db.add(run)
    db.add(OutboxEvent(tenant_id="tenant", topic="run.dispatch", payload={"run_id": "run"}))
    db.commit()
    run, fence = claim_run(db, run.id, "worker")
    transition(db, run, "worker", fence, "PREPARING")
    transition(db, run, "worker", fence, "PAUSED_INPUT")
    db.commit()
    return run


def test_resume_input_requeues_once_and_preserves_answer():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = _paused(db)
        assert run.resume_target == "PREPARING"
        assert run.lease_owner is None
        resumed = resume_input_run(
            db, "tenant", run.id, "actor", "Use the valid form", "resume-key-001"
        )
        db.commit()
        assert resumed.state == "QUEUED"
        events = db.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at)).all()
        assert len(events) == 2
        assert sorted(event.status for event in events) == ["delivered", "pending"]
        assert (
            len(db.scalars(select(RunEvent).where(RunEvent.event_type == "run.resumed")).all()) == 1
        )
        assert (
            resume_input_run(
                db, "tenant", run.id, "actor", "Use the valid form", "resume-key-001"
            ).id
            == run.id
        )
        db.commit()
        assert len(db.scalars(select(OutboxEvent)).all()) == 2
        with pytest.raises(ServiceError) as conflict:
            resume_input_run(db, "tenant", run.id, "actor", "Try another form", "resume-key-001")
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    engine.dispose()


def test_resume_refuses_uncertain_effect_and_changed_policy():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = _paused(db)
        db.add(
            ToolAction(
                tenant_id="tenant",
                run_id=run.id,
                step_id="browser",
                logical_action="fixture.browser",
                effect_key="e" * 64,
                arguments_hash="a" * 64,
                policy_result="allowed",
                status="INTENDED",
            )
        )
        db.commit()
        with pytest.raises(ServiceError) as uncertain:
            resume_input_run(db, "tenant", run.id, "actor", "Use the valid form", "resume-key-001")
        assert uncertain.value.code == "EFFECT_OUTCOME_UNKNOWN"
        action = db.scalar(select(ToolAction))
        action.status = "COMPLETED"
        db.get(Tenant, "tenant").policy_revision = "2.0"
        db.commit()
        with pytest.raises(ServiceError) as policy:
            resume_input_run(db, "tenant", run.id, "actor", "Use the valid form", "resume-key-001")
        assert policy.value.code == "POLICY_REVIEW_REQUIRED"
    engine.dispose()
