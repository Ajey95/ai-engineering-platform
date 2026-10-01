import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.memory import delete_fact, propose_fact, scoped_lookup, verify_fact
from platform_app.models import Project, Tenant
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
    with pytest.raises(ServiceError, match="linked"):
        verify_fact(db, fact, "model:claim", "unit tests", "test tool")
    verify_fact(db, fact, "test:123", "unit tests only", "test tool")
    db.commit()
    assert len(scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload")) == 1
    assert scoped_lookup(db, "tenant-a", "project-a", "b" * 40, "upload") == []
    assert scoped_lookup(db, "tenant-b", "project-a", "a" * 40, "upload") == []
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "%") == []
    delete_fact(db, fact)
    db.commit()
    assert scoped_lookup(db, "tenant-a", "project-a", "a" * 40, "upload") == []
