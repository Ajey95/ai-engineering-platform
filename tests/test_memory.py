from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.memory import (
    delete_fact,
    expire_due_facts,
    expire_fact,
    propose_fact,
    reject_fact,
    scoped_lookup,
    select_context_facts,
    supersede_fact,
    verify_fact,
)
from platform_app.models import MemoryFactEvent, OutboxEvent, Project, Tenant
from platform_app.service import ServiceError


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="A"),
                Tenant(id="tenant-b", name="B"),
                Project(id="project-a", tenant_id="tenant-a", name="Project A"),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def test_unverified_stale_and_cross_tenant_facts_are_excluded(db):
    fact = propose_fact(
        db,
        "tenant-a",
        "project-a",
        "repo",
        "a" * 40,
        "project_fact",
        "upload.py",
        "Upload limit is 5 MB",
        ["test:123"],
    )
    db.commit()
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == []
    assert select_context_facts(
        db, "tenant-a", "project-a", "a" * 40, "Upload fails"
    ) == []


    with pytest.raises(ServiceError, match="linked"):
        verify_fact(db, fact, "model:claim", "unit tests", "test tool")
    verify_fact(db, fact, "test:123", "unit tests only", "test tool")
    db.commit()
    assert len(scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload")) == 1
    assert select_context_facts(
        db, "tenant-a", "project-a", "a" * 40, "Upload fails for large files"
    ) == [fact]
    assert select_context_facts(
        db, "tenant-a", "project-a", "b" * 40, "Upload fails"
    ) == []
    assert scoped_lookup(db, "tenant-a", "project-a", "b" * 40, "upload") == []
    assert scoped_lookup(db, "tenant-b", "project-a", "a" * 40, "upload") == []
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "%") == []
    delete_fact(db, fact)
    db.commit()
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == []
    assert select_context_facts(
        db, "tenant-a", "project-a", "a" * 40, "Upload fails"
    ) == []


def test_rejection_supersession_and_expiry_revoke_retrieval(db):
    def fact(statement: str):
        return propose_fact(
            db, "tenant-a", "project-a", "repo", "a" * 40,
            "project_fact", "upload.py", statement, ["test:123"], actor="reviewer",
        )

    unsupported = fact("Unsupported upload limit")
    reject_fact(db, unsupported, "reviewer", "No independent evidence")
    with pytest.raises(ServiceError, match="Only proposed"):
        verify_fact(db, unsupported, "test:123", "unit tests", "tool")
    original = fact("Upload limit is 5 MB")
    replacement = fact("Upload limit is 10 MB")
    verify_fact(db, original, "test:123", "unit tests only", "tool")
    verify_fact(db, replacement, "test:123", "unit tests only", "tool")
    db.commit()
    assert len(scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload")) == 2
    supersede_fact(db, original, replacement, "reviewer", "New evidence changes limit")
    db.commit()
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == [replacement]
    expire_fact(db, replacement, "maintainer", "Environment configuration changed")
    db.commit()
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == []
    events = db.query(MemoryFactEvent).filter(
        MemoryFactEvent.tenant_id == "tenant-a"
    ).all()
    assert {event.status for event in events} >= {
        "proposed", "verified", "rejected", "superseded", "expired"
    }
    assert next(event for event in events if event.status == "superseded").evidence_ref == (
        replacement.id
    )
    with pytest.raises(ServiceError, match="Both facts"):
        supersede_fact(db, original, replacement, "reviewer", "Repeated stale claim")


def test_environment_observation_expires_once_and_projects_tombstone(db):
    fact = propose_fact(
        db, "tenant-a", "project-a", "repo", "a" * 40,
        "environment_observation", "staging upload", "Upload service is offline",
        ["test:offline"],
    )
    verify_fact(db, fact, "test:offline", "staging probe only", "test tool")
    db.commit()
    assert fact.valid_until - fact.valid_from == timedelta(hours=24)
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == [fact]
    expiry = fact.valid_until
    assert expire_due_facts(db, now=expiry - timedelta(seconds=1)) == 0
    assert expire_due_facts(db, now=expiry + timedelta(seconds=1)) == 1
    assert fact.status == "expired"
    assert fact.valid_until <= expiry
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == []
    assert expire_due_facts(db, now=expiry + timedelta(days=1)) == 0
    assert db.query(OutboxEvent).filter_by(topic="memory.project").count() == 2
    events = db.query(MemoryFactEvent).filter_by(fact_id=fact.id).all()
    assert [event.status for event in events] == ["proposed", "verified", "expired"]


def test_cannot_supersede_with_stale_verified_replacement(db):
    original = propose_fact(
        db, "tenant-a", "project-a", "repo", "a" * 40,
        "project_fact", "upload", "Limit is 5 MB", ["test:one"],
    )
    replacement = propose_fact(
        db, "tenant-a", "project-a", "repo", "a" * 40,
        "project_fact", "upload", "Limit is 10 MB", ["test:two"],
    )
    verify_fact(db, original, "test:one", "unit", "tool")
    verify_fact(db, replacement, "test:two", "unit", "tool")
    replacement.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ServiceError, match="not current"):
        supersede_fact(db, original, replacement, "reviewer", "A new test changed the limit")
    assert original.status == "verified"
