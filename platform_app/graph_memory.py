"""Rebuildable Memgraph projection of canonical, verified PostgreSQL memory."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from platform_app.db import utcnow
from platform_app.memory import scoped_lookup
from platform_app.models import MemoryFact, OutboxEvent


class GraphUnavailable(Exception):
    pass


class GraphProjection(Protocol):
    def clear_scope(self, tenant_id: str, project_id: str) -> None: ...
    def upsert_fact(self, fact: MemoryFact) -> None: ...
    def delete_fact(self, tenant_id: str, fact_id: str) -> None: ...
    def find_fact_ids(
        self, tenant_id: str, project_id: str, revision: str, query: str, limit: int
    ) -> list[str]: ...


class MemgraphProjection:
    def __init__(self, uri: str, username: str = "", password: str = ""):
        if not uri.startswith(("bolt://", "bolt+s://", "bolt+ssc://")):
            raise GraphUnavailable("Memgraph URI must use Bolt")
        auth = (username, password) if username else None
        try:
            self.driver = GraphDatabase.driver(uri, auth=auth, connection_timeout=3)
            self.driver.verify_connectivity()
        except (Neo4jError, ServiceUnavailable, SessionExpired, ValueError) as error:
            if hasattr(self, "driver"):
                self.driver.close()
            raise GraphUnavailable("Memgraph is unavailable") from error

    def close(self) -> None:
        self.driver.close()

    def _write(self, query: str, **params) -> None:
        try:
            with self.driver.session() as session:
                session.run(query, **params).consume()
        except (Neo4jError, ServiceUnavailable, SessionExpired) as error:
            raise GraphUnavailable("Memgraph write failed") from error

    def upsert_fact(self, fact: MemoryFact) -> None:
        self._write(
            """
            MERGE (p:Project {tenant_id: $tenant_id, id: $project_id})
            SET p.project_id = $project_id
            MERGE (r:Repository {tenant_id: $tenant_id, project_id: $project_id,
                                 ref: $repository_ref})
            MERGE (v:SourceRevision {tenant_id: $tenant_id, project_id: $project_id,
                                     repository_ref: $repository_ref, revision: $revision})
            MERGE (s:Subject {tenant_id: $tenant_id, project_id: $project_id,
                              name: $subject})
            MERGE (f:Fact {id: $fact_id})
            SET f.tenant_id = $tenant_id, f.project_id = $project_id,
                f.source_revision = $revision, f.statement = $statement,
                f.fact_type = $fact_type, f.subject = $subject,
                f.verification_scope = $verification_scope,
                f.valid_from = $valid_from, f.source_refs = $source_refs
            MERGE (p)-[:HAS_REPOSITORY]->(r)
            MERGE (r)-[:HAS_REVISION]->(v)
            MERGE (p)-[:HAS_FACT]->(f)
            MERGE (f)-[:AT_REVISION]->(v)
            MERGE (f)-[:ABOUT]->(s)
            """,
            tenant_id=fact.tenant_id,
            project_id=fact.project_id,
            repository_ref=fact.repository_ref,
            revision=fact.source_revision,
            subject=fact.subject,
            fact_id=fact.id,
            statement=fact.statement,
            fact_type=fact.fact_type,
            verification_scope=fact.verification_scope or "",
            valid_from=fact.valid_from.isoformat() if fact.valid_from else "",
            source_refs=fact.source_refs,
        )

    def delete_fact(self, tenant_id: str, fact_id: str) -> None:
        self._write(
            "MATCH (f:Fact {id: $fact_id, tenant_id: $tenant_id}) DETACH DELETE f",
            fact_id=fact_id, tenant_id=tenant_id,
        )

    def clear_scope(self, tenant_id: str, project_id: str) -> None:
        self._write(
            "MATCH (n) WHERE n.tenant_id = $tenant_id AND n.project_id = $project_id "
            "DETACH DELETE n",
            tenant_id=tenant_id, project_id=project_id,
        )

    def find_fact_ids(
        self, tenant_id: str, project_id: str, revision: str, query: str, limit: int
    ) -> list[str]:
        try:
            with self.driver.session() as session:
                rows = session.run(
                    """
                    MATCH (p:Project {tenant_id: $tenant_id, id: $project_id})
                          -[:HAS_FACT]->(f:Fact)-[:AT_REVISION]->
                          (v:SourceRevision {revision: $revision})
                    WHERE f.tenant_id = $tenant_id AND f.project_id = $project_id
                      AND (toLower(f.statement) CONTAINS $needle
                           OR toLower(f.subject) CONTAINS $needle
                           OR toLower(f.fact_type) CONTAINS $needle
                           OR toLower(f.source_revision) CONTAINS $needle)
                    RETURN f.id AS id ORDER BY f.valid_from DESC, f.id LIMIT $limit
                    """,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    revision=revision,
                    needle=query.lower(),
                    limit=limit,
                )
                return [row["id"] for row in rows]
        except (Neo4jError, ServiceUnavailable, SessionExpired) as error:
            raise GraphUnavailable("Memgraph read failed") from error


def project_next(db: Session, graph: GraphProjection) -> str | None:
    """An outbox retry projects current canonical state, so old events cannot resurrect facts."""
    event = db.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.topic == "memory.project", OutboxEvent.status == "pending")
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if event is None:
        return None
    fact_id = event.payload.get("fact_id")
    fact = db.get(MemoryFact, fact_id) if isinstance(fact_id, str) else None
    if fact is None or fact.tenant_id != event.tenant_id:
        event.status = "failed"
        event.attempts += 1
        db.commit()
        return event.id
    valid_until = fact.valid_until
    if valid_until and valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=UTC)
    if fact.status == "verified" and (valid_until is None or valid_until > utcnow()):
        graph.upsert_fact(fact)
    else:
        graph.delete_fact(fact.tenant_id, fact.id)
    event.status = "delivered"
    event.attempts += 1
    db.commit()
    return event.id


def rebuild_scope(
    db: Session, graph: GraphProjection, tenant_id: str, project_id: str
) -> int:
    """Recreate one derived project graph from its current canonical records."""
    facts = db.scalars(
        select(MemoryFact).where(
            MemoryFact.tenant_id == tenant_id,
            MemoryFact.project_id == project_id,
            MemoryFact.status == "verified",
            MemoryFact.valid_from.is_not(None),
            or_(MemoryFact.valid_until.is_(None), MemoryFact.valid_until > utcnow()),
        )
    ).all()
    graph.clear_scope(tenant_id, project_id)
    for fact in facts:
        graph.upsert_fact(fact)
    return len(facts)


def connected_lookup(
    db: Session,
    graph: GraphProjection | None,
    tenant_id: str,
    project_id: str,
    source_revision: str,
    query: str,
    limit: int = 10,
) -> tuple[str, list[MemoryFact]]:
    """Graph IDs are hints; every returned fact is rechecked in PostgreSQL."""
    canonical = scoped_lookup(db, tenant_id, project_id, source_revision, query, limit)
    pending = db.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.tenant_id == tenant_id,
            OutboxEvent.topic == "memory.project",
            OutboxEvent.status == "pending",
        )
        .limit(1)
    )
    if graph is None or pending is not None:
        return "canonical_degraded", canonical
    try:
        fact_ids = graph.find_fact_ids(tenant_id, project_id, source_revision, query, limit)
    except GraphUnavailable:
        return "canonical_degraded", canonical
    if not fact_ids:
        return ("graph", []) if not canonical else ("canonical_degraded", canonical)
    now = datetime.now(UTC)
    escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    facts = db.scalars(
        select(MemoryFact).where(
            MemoryFact.id.in_(fact_ids),
            MemoryFact.tenant_id == tenant_id,
            MemoryFact.project_id == project_id,
            MemoryFact.source_revision == source_revision,
            MemoryFact.status == "verified",
            MemoryFact.valid_from.is_not(None),
            MemoryFact.valid_from <= now,
            or_(MemoryFact.valid_until.is_(None), MemoryFact.valid_until > now),
            or_(
                MemoryFact.subject.ilike(f"%{escaped_query}%", escape="\\"),
                MemoryFact.statement.ilike(f"%{escaped_query}%", escape="\\"),
            ),
        )
    ).all()
    by_id = {fact.id: fact for fact in facts}
    if set(by_id) != {fact.id for fact in canonical}:
        return "canonical_degraded", canonical
    return "graph", [
        by_id[fact_id] for fact_id in dict.fromkeys(fact_ids) if fact_id in by_id
    ]
