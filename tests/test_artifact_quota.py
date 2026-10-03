"""Private media byte reservations are durable and tenant scoped."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.artifact_quota import (
    activate_artifact,
    artifact_usage_bytes,
    delete_artifact_charge,
    reserve_artifact,
)
from platform_app.db import Base
from platform_app.models import ArtifactCharge, AuditEvent, Project, Run, Task, Tenant
from platform_app.service import ServiceError


def _run(db: Session, name: str) -> Run:
    project = Project(id=f"project-{name}", tenant_id="tenant", name=name)
    task = Task(
        id=f"task-{name}", tenant_id="tenant", project_id=project.id,
        report="bug", expected_behavior="works", actual_behavior="broken",
        created_by="owner",
    )
    run = Run(
        id=f"run-{name}", tenant_id="tenant", project_id=project.id,
        task_id=task.id, created_by="owner", idempotency_key=f"key-{name}",
        request_hash="b" * 64, base_commit="c" * 40, model_entry_id="model",
        state="REVIEW_READY", config_snapshot={"policy_version": "1.0"},
    )
    db.add_all([project, task, run])
    db.flush()
    return run


def test_artifact_bytes_reserve_activate_and_release_only_after_deletion():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant", name="Tenant", artifact_cap_bytes=100))
        db.flush()
        first = _run(db, "first")
        second = _run(db, "second")
        charge = reserve_artifact(db, first.id, "private_media", "baseline", "a" * 64, 80)
        db.commit()
        assert artifact_usage_bytes(db, "tenant") == 80
        assert reserve_artifact(
            db, first.id, "private_media", "baseline", "a" * 64, 80
        ).id == charge.id
        assert len(db.scalars(select(AuditEvent).where(
            AuditEvent.action == "artifact.threshold_80"
        )).all()) == 1
        try:
            reserve_artifact(db, second.id, "private_media", "candidate", "b" * 64, 21)
        except ServiceError as error:
            assert error.code == "ARTIFACT_QUOTA_EXHAUSTED"
        else:
            raise AssertionError("Over-cap reservation was admitted")
        db.rollback()
        charge = db.get(ArtifactCharge, charge.id)
        activate_artifact(db, charge)
        db.commit()
        assert charge.status == "active" and artifact_usage_bytes(db, "tenant") == 80
        delete_artifact_charge(db, first, "private_media", "baseline")
        db.commit()
        assert artifact_usage_bytes(db, "tenant") == 0
        assert charge.status == "deleted"
        reserve_artifact(db, second.id, "private_media", "candidate", "b" * 64, 100)
        db.commit()
        assert artifact_usage_bytes(db, "tenant") == 100
    engine.dispose()


def test_artifact_reservation_rejects_changed_bytes_and_deleted_replay():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant", name="Tenant", artifact_cap_bytes=100))
        db.flush()
        run = _run(db, "one")
        reserve_artifact(db, run.id, "private_media", "baseline", "a" * 64, 10)
        db.commit()
        for digest, size in [("b" * 64, 10), ("a" * 64, 11)]:
            try:
                reserve_artifact(db, run.id, "private_media", "baseline", digest, size)
            except ServiceError as error:
                assert error.code == "ARTIFACT_CONFLICT"
            else:
                raise AssertionError("Changed artifact reservation was accepted")
            db.rollback()
        delete_artifact_charge(db, run, "private_media", "baseline")
        db.commit()
        try:
            reserve_artifact(db, run.id, "private_media", "baseline", "a" * 64, 10)
        except ServiceError as error:
            assert error.code == "ARTIFACT_CONFLICT"
        else:
            raise AssertionError("Deleted recording reservation was replayed")
    engine.dispose()
