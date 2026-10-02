import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api
from platform_app.db import Base
from platform_app.graph_memory import (
    GraphUnavailable,
    MemgraphProjection,
    connected_lookup,
    project_next,
    rebuild_scope,
)
from platform_app.memory import delete_fact, propose_fact, verify_fact
from platform_app.models import OutboxEvent, Project, Tenant


class FakeGraph:
    def __init__(self):
        self.facts = {}
        self.fail = False

    def upsert_fact(self, fact):
        if self.fail:
            raise GraphUnavailable("offline")
        self.facts[fact.id] = fact

    def delete_fact(self, tenant_id, fact_id):
        if self.fail:
            raise GraphUnavailable("offline")
        self.facts.pop(fact_id, None)

    def clear_scope(self, tenant_id, project_id):
        if self.fail:
            raise GraphUnavailable("offline")
        self.facts = {
            fact_id: fact for fact_id, fact in self.facts.items()
            if (fact.tenant_id, fact.project_id) != (tenant_id, project_id)
        }

    def find_fact_ids(self, tenant_id, project_id, revision, query, limit):
        if self.fail:
            raise GraphUnavailable("offline")
        return list(self.facts)[:limit]

    def close(self):
        pass


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Tenant(id="tenant-a", name="A"),
            Tenant(id="tenant-b", name="B"),
            Project(id="project-a", tenant_id="tenant-a", name="A"),
        ])
        session.commit()
        yield session
    engine.dispose()


def verified_fact(db):
    fact = propose_fact(
        db, "tenant-a", "project-a", "repo", "a" * 40, "project_fact",
        "upload.py", "Upload limit is 5 MB", ["test:123"],
    )
    verify_fact(db, fact, "test:123", "unit tests", "test tool")
    db.commit()
    return fact


def test_outbox_projection_and_canonical_scope_recheck(db):
    fact = verified_fact(db)
    graph = FakeGraph()
    mode, facts = connected_lookup(db, graph, "tenant-a", "project-a", "a" * 40, "upload")
    assert mode == "canonical_degraded" and facts == [fact]
    assert project_next(db, graph) is not None
    mode, facts = connected_lookup(db, graph, "tenant-a", "project-a", "a" * 40, "upload")
    assert mode == "graph" and facts == [fact]
    graph.fail = True
    assert connected_lookup(db, graph, "tenant-a", "project-a", "a" * 40, "upload") == (
        "canonical_degraded", [fact]
    )
    graph.fail = False
    assert connected_lookup(db, graph, "tenant-b", "project-a", "a" * 40, "upload")[1] == []
    assert connected_lookup(db, graph, "tenant-a", "project-a", "b" * 40, "upload")[1] == []
    assert connected_lookup(db, graph, "tenant-a", "project-a", "a" * 40, "password")[1] == []
    delete_fact(db, fact)
    db.commit()
    mode, facts = connected_lookup(db, graph, "tenant-a", "project-a", "a" * 40, "upload")
    assert mode == "canonical_degraded" and facts == []
    assert project_next(db, graph) is not None
    assert fact.id not in graph.facts


def test_reordered_outbox_and_outage_cannot_resurrect_deleted_fact(db):
    fact = verified_fact(db)
    graph = FakeGraph()
    graph.fail = True
    with pytest.raises(GraphUnavailable):
        project_next(db, graph)
    db.rollback()
    assert db.scalar(select(OutboxEvent.status)) == "pending"
    assert connected_lookup(db, graph, "tenant-a", "project-a", "a" * 40, "upload") == (
        "canonical_degraded", [fact]
    )
    graph.fail = False
    delete_fact(db, fact)
    db.commit()
    assert project_next(db, graph) is not None
    assert project_next(db, graph) is not None
    assert graph.facts == {}


def test_rebuild_scope_restores_and_removes_derived_records(db):
    fact = verified_fact(db)
    graph = FakeGraph()
    assert rebuild_scope(db, graph, "tenant-a", "project-a") == 1
    assert fact.id in graph.facts
    delete_fact(db, fact)
    db.commit()
    assert rebuild_scope(db, graph, "tenant-a", "project-a") == 0
    assert graph.facts == {}


def test_memory_api_exposes_graph_or_canonical_mode_without_cross_tenant_data(
    db, monkeypatch
):
    from types import SimpleNamespace

    fact = verified_fact(db)
    graph = FakeGraph()
    project_next(db, graph)
    monkeypatch.setattr(api, "MemgraphProjection", lambda *args: graph)
    monkeypatch.setattr(api, "settings", lambda: SimpleNamespace(
        memgraph_uri="bolt://local", memgraph_user="", memgraph_password="",
        environment="development", dev_token="",
    ))

    def session_override():
        yield db

    api.app.dependency_overrides[api.db_session] = session_override
    api.app.dependency_overrides[api.principal] = lambda: ("tenant-a", "actor")
    try:
        client = TestClient(api.app)
        path = f"/v1/projects/project-a/memory?source_revision={'a' * 40}&query=upload"
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["retrieval_mode"] == "graph"
        assert response.json()["facts"][0]["id"] == fact.id
        graph.fail = True
        assert client.get(path).json()["retrieval_mode"] == "canonical_degraded"
        api.app.dependency_overrides[api.principal] = lambda: ("tenant-b", "actor")
        assert client.get(path).status_code == 404
    finally:
        api.app.dependency_overrides.clear()


def test_real_memgraph_projection_roundtrip_when_configured(db):
    uri = os.environ.get("AIP_TEST_MEMGRAPH_URI")
    if not uri:
        pytest.skip("Set AIP_TEST_MEMGRAPH_URI for the Memgraph integration gate")
    fact = verified_fact(db)
    graph = MemgraphProjection(uri)
    try:
        assert project_next(db, graph) is not None
        assert fact.id in graph.find_fact_ids(
            "tenant-a", "project-a", "a" * 40, "upload", 50
        )
        delete_fact(db, fact)
        db.commit()
        assert project_next(db, graph) is not None
        assert fact.id not in graph.find_fact_ids(
            "tenant-a", "project-a", "a" * 40, "upload", 50
        )
        assert rebuild_scope(db, graph, "tenant-a", "project-a") == 0
    finally:
        graph.delete_fact("tenant-a", fact.id)
        graph.close()
