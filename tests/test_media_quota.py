from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.media_quota import (
    finish_media_attempt,
    media_usage_seconds,
    reserve_media_attempt,
)
from platform_app.models import AuditEvent, MediaMinuteCharge, Project, Run, Task, Tenant
from platform_app.service import ServiceError


def test_media_minutes_book_each_encode_attempt_and_block_at_tenant_cap():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant", name="Tenant", daily_media_minutes=2))
        db.add(Project(id="project", tenant_id="tenant", name="Project"))
        db.add(Task(
            id="task", tenant_id="tenant", project_id="project", report="Bug",
            expected_behavior="works", actual_behavior="broken", created_by="owner",
        ))
        db.add(Run(
            id="run", tenant_id="tenant", project_id="project", task_id="task",
            created_by="owner", idempotency_key="run", request_hash="a" * 64,
            base_commit="b" * 40, model_entry_id="fixture", state="COMPLETED",
            config_snapshot={},
        ))
        db.commit()
        first = reserve_media_attempt(db, "run", "baseline", 1, "c" * 64, 70.1)
        db.commit()
        assert first.reserved_seconds == 71
        assert reserve_media_attempt(db, "run", "baseline", 1, "c" * 64, 70.1).id == first.id
        db.commit()
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "media.threshold_80",
        )) is None
        with pytest.raises(ServiceError) as exceeded:
            reserve_media_attempt(db, "run", "baseline", 2, "c" * 64, 70.1)
        assert exceeded.value.code == "MEDIA_QUOTA_EXHAUSTED"
        db.rollback()
        second = reserve_media_attempt(db, "run", "candidate", 1, "d" * 64, 48.2)
        db.commit()
        assert second.reserved_seconds == 49
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "media.threshold_80",
        )) is not None
        finish_media_attempt(db, first.id, succeeded=False)
        finish_media_attempt(db, second.id, succeeded=True)
        db.commit()
        assert media_usage_seconds(db, "tenant", now=datetime.now(UTC)) == 120
        assert [row.status for row in db.scalars(select(MediaMinuteCharge).order_by(
            MediaMinuteCharge.label,
        ))] == ["failed", "completed"]
    engine.dispose()
