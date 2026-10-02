import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.db import Base
from platform_app.models import AuditEvent, MemoryFact, Project, Run, RunEvent, Tenant
from platform_app.service import ServiceError, record_review_decision, request_cancel


def _run() -> Run:
    return Run(
        id="11111111-1111-4111-8111-111111111111",
        tenant_id="fixture-tenant", task_id="task", project_id="project",
        created_by="reviewer", idempotency_key="key", request_hash="r" * 64,
        base_commit="b" * 40, model_entry_id="model", state="REVIEW_READY",
        verdict="PASSED", config_snapshot={"policy_version": "1.0"},
    )


def _seed(db: Session) -> Run:
    run = _run()
    db.add_all([
        Tenant(id="fixture-tenant", name="Fixture"),
        Project(id="project", tenant_id="fixture-tenant", name="Project"),
        run,
    ])
    db.commit()
    return run


def test_review_decision_is_durable_idempotent_and_preserves_verdict(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'review.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        _seed(db)
        run = record_review_decision(
            db, "fixture-tenant", "11111111-1111-4111-8111-111111111111",
            "reviewer", "accepted",
        )
        db.commit()
        assert run.state == "COMPLETED"
        assert run.verdict == "PASSED"
        assert record_review_decision(
            db, run.tenant_id, run.id, "reviewer", "accepted"
        ).id == run.id
        db.commit()
        events = db.scalars(select(RunEvent).where(
            RunEvent.run_id == run.id, RunEvent.event_type == "review.decision"
        )).all()
        assert len(events) == 1
        assert events[0].payload["decision"] == "accepted"
        facts = db.scalars(select(MemoryFact).where(
            MemoryFact.tenant_id == run.tenant_id, MemoryFact.project_id == run.project_id,
        )).all()
        assert len(facts) == 1 and facts[0].status == "verified"
        assert facts[0].source_refs == [f"run-event:{events[0].id}"]
        assert "not independent repair validation" in facts[0].verification_scope
        audits = db.scalars(select(AuditEvent).where(AuditEvent.target_ref == run.id)).all()
        assert len(audits) == 1
        with pytest.raises(ServiceError) as conflict:
            record_review_decision(db, run.tenant_id, run.id, "reviewer", "rejected", "bad fix")
        assert conflict.value.code == "REVIEW_CLOSED"
        with pytest.raises(ServiceError) as foreign:
            record_review_decision(db, "other-tenant", run.id, "reviewer", "accepted")
        assert foreign.value.status == 404


def test_rejection_requires_reason_and_cancel_keeps_verified_result(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'review.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = _seed(db)
        with pytest.raises(ServiceError) as missing:
            record_review_decision(db, run.tenant_id, run.id, "reviewer", "rejected", "no")
        assert missing.value.code == "REASON_REQUIRED"
        request_cancel(db, run, "reviewer")
        db.commit()
        assert run.state == "CANCELLED"
        assert run.verdict == "PASSED"


def test_rejected_review_records_decision_but_does_not_validate_repair(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'rejected.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = _seed(db)
        record_review_decision(
            db, run.tenant_id, run.id, "reviewer", "rejected", "Fails accessibility check"
        )
        db.commit()
        fact = db.scalar(select(MemoryFact).where(MemoryFact.project_id == "project"))
        assert fact.status == "verified" and fact.fact_type == "decision"
        assert "rejected" in fact.statement and "Fails accessibility" in fact.statement
        assert fact.source_revision == run.base_commit
    engine.dispose()
