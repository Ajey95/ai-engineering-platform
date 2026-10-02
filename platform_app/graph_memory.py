"""Rebuildable Memgraph projection of canonical, verified PostgreSQL memory."""

from __future__ import annotations

import hashlib
import posixpath
from datetime import UTC, datetime
from typing import Protocol

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from platform_app.code_index import code_files_for_revision
from platform_app.db import utcnow
from platform_app.memory import scoped_lookup
from platform_app.models import CodeFileVersion, CodeIndexSnapshot, MemoryFact, OutboxEvent
from platform_app.service import ServiceError


class GraphUnavailable(Exception):
    pass


def _resolved_dependencies(files: list[CodeFileVersion]) -> list[dict[str, str]]:
    by_path = {file.path: file.id for file in files}
    edges = set()
    for file in files:
        for imported in file.imports:
            if not isinstance(imported, str) or not imported:
                continue
            if file.language == "python":
                if imported.startswith("."):
                    dots = len(imported) - len(imported.lstrip("."))
                    base = posixpath.dirname(file.path)
                    for _ in range(dots - 1):
                        base = posixpath.dirname(base)
                    target = posixpath.join(base, imported[dots:].replace(".", "/"))
                else:
                    target = imported.replace(".", "/")
                candidates = [f"{target}.py", f"{target}/__init__.py"]
            elif imported.startswith("."):
                target = posixpath.normpath(posixpath.join(
                    posixpath.dirname(file.path), imported
                ))
                candidates = [
                    target + suffix for suffix in ("", ".js", ".jsx", ".ts", ".tsx")
                ] + [
                    f"{target}/index{suffix}" for suffix in (".js", ".jsx", ".ts", ".tsx")
                ]
            else:
                continue
            for candidate in candidates:
                if candidate in by_path and candidate != file.path:
                    edges.add((file.id, by_path[candidate]))
                    break
    return [
        {"source_id": source, "target_id": target}
        for source, target in sorted(edges)
    ]


class GraphProjection(Protocol):
    def clear_scope(self, tenant_id: str, project_id: str) -> None: ...
    def upsert_fact(self, fact: MemoryFact) -> None: ...
    def delete_fact(self, tenant_id: str, fact_id: str) -> None: ...
    def upsert_code_snapshot(
        self, snapshot: CodeIndexSnapshot, files: list[CodeFileVersion]
    ) -> None: ...
    def find_fact_ids(
        self, tenant_id: str, project_id: str, revision: str, query: str, limit: int
    ) -> list[str]: ...
    def find_file_ids(
        self, tenant_id: str, project_id: str, repository_ref: str,
        revision: str, query: str, limit: int,
    ) -> list[str]: ...
    def find_dependency_ids(
        self, tenant_id: str, project_id: str, repository_ref: str,
        revision: str, path: str, depth: int, limit: int,
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

    def upsert_code_snapshot(
        self, snapshot: CodeIndexSnapshot, files: list[CodeFileVersion]
    ) -> None:
        """Immutable file/symbol edges always carry the source commit."""
        scope = {
            "tenant_id": snapshot.tenant_id,
            "project_id": snapshot.project_id,
            "repository_ref": snapshot.repository_ref,
            "revision": snapshot.commit,
            "snapshot_id": snapshot.id,
        }
        self._write(
            """
            MERGE (p:Project {tenant_id: $tenant_id, id: $project_id})
            SET p.project_id = $project_id
            MERGE (r:Repository {tenant_id: $tenant_id, project_id: $project_id,
                                 ref: $repository_ref})
            MERGE (c:Commit {tenant_id: $tenant_id, project_id: $project_id,
                             repository_ref: $repository_ref, revision: $revision})
            SET c.canonical_id = $snapshot_id, c.provenance_ref = $snapshot_id,
                c.status = 'indexed'
            MERGE (p)-[:HAS_REPOSITORY]->(r)
            MERGE (r)-[:HAS_VERSION]->(c)
            """, **scope,
        )
        for start in range(0, len(files), 50):
            batch = files[start : start + 50]
            self._write(
                """
                MATCH (c:Commit {tenant_id: $tenant_id, project_id: $project_id,
                                 repository_ref: $repository_ref, revision: $revision})
                UNWIND $files AS item
                MERGE (f:FileVersion {canonical_id: item.id})
                SET f.tenant_id = $tenant_id, f.project_id = $project_id,
                    f.repository_ref = $repository_ref, f.revision = $revision,
                    f.path = item.path, f.sha256 = item.sha256,
                    f.language = item.language, f.provenance_ref = $snapshot_id,
                    f.status = 'indexed'
                MERGE (c)-[e:HAS_VERSION]->(f)
                SET e.tenant_id = $tenant_id, e.project_id = $project_id,
                    e.revision = $revision, e.provenance_ref = $snapshot_id,
                    e.status = 'indexed'
                """,
                **scope,
                files=[{
                    "id": file.id, "path": file.path,
                    "sha256": file.sha256, "language": file.language,
                } for file in batch],
            )
            symbols = [
                {
                    "id": hashlib.sha256(
                        f"{file.id}:{item['qualified_name']}:{item['line']}".encode()
                    ).hexdigest(),
                    "file_id": file.id,
                    "file_sha256": file.sha256,
                    **item,
                }
                for file in batch for item in file.symbols
            ]
            if symbols:
                self._write(
                    """
                    UNWIND $symbols AS item
                    MATCH (f:FileVersion {canonical_id: item.file_id})
                    MERGE (s:SymbolVersion {canonical_id: item.id})
                    SET s.tenant_id = $tenant_id, s.project_id = $project_id,
                        s.repository_ref = $repository_ref, s.revision = $revision,
                        s.file_sha256 = item.file_sha256,
                        s.qualified_name = item.qualified_name,
                        s.kind = item.kind, s.line = item.line,
                        s.provenance_ref = $snapshot_id, s.status = 'indexed'
                    MERGE (f)-[e:HAS_VERSION]->(s)
                    SET e.tenant_id = $tenant_id, e.project_id = $project_id,
                        e.revision = $revision, e.provenance_ref = $snapshot_id,
                        e.status = 'indexed'
                    """,
                    **scope, symbols=symbols,
                )
        edges = _resolved_dependencies(files)
        for start in range(0, len(edges), 100):
            self._write(
                """
                UNWIND $edges AS edge
                MATCH (a:FileVersion {canonical_id: edge.source_id})
                MATCH (b:FileVersion {canonical_id: edge.target_id})
                MERGE (a)-[e:DEPENDS_ON]->(b)
                SET e.tenant_id = $tenant_id, e.project_id = $project_id,
                    e.revision = $revision, e.provenance_ref = $snapshot_id,
                    e.status = 'indexed'
                """,
                **scope, edges=edges[start : start + 100],
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

    def find_file_ids(
        self, tenant_id: str, project_id: str, repository_ref: str,
        revision: str, query: str, limit: int,
    ) -> list[str]:
        try:
            with self.driver.session() as session:
                rows = session.run(
                    """
                    MATCH (c:Commit {tenant_id: $tenant_id,
                                     project_id: $project_id,
                                     repository_ref: $repository_ref,
                                     revision: $revision})-[:HAS_VERSION]->(f:FileVersion)
                    OPTIONAL MATCH (f)-[:HAS_VERSION]->(s:SymbolVersion)
                    WITH f, s
                    WHERE toLower(f.path) CONTAINS $needle
                       OR toLower(coalesce(s.qualified_name, '')) CONTAINS $needle
                    RETURN DISTINCT f.canonical_id AS id ORDER BY id LIMIT $limit
                    """,
                    tenant_id=tenant_id, project_id=project_id,
                    repository_ref=repository_ref, revision=revision,
                    needle=query.lower(), limit=limit,
                )
                return [row["id"] for row in rows]
        except (Neo4jError, ServiceUnavailable, SessionExpired) as error:
            raise GraphUnavailable("Memgraph code read failed") from error

    def find_dependency_ids(
        self, tenant_id: str, project_id: str, repository_ref: str,
        revision: str, path: str, depth: int, limit: int,
    ) -> list[str]:
        if depth not in {1, 2}:
            raise ValueError("Dependency depth must be one or two")
        hops = "1..1" if depth == 1 else "1..2"
        query = f"""
            MATCH (c:Commit {{tenant_id: $tenant_id, project_id: $project_id,
                              repository_ref: $repository_ref, revision: $revision}})
                  -[:HAS_VERSION]->(root:FileVersion {{path: $path}})
            OPTIONAL MATCH (root)-[:DEPENDS_ON*{hops}]-(neighbor:FileVersion)
            WHERE neighbor.tenant_id = $tenant_id
              AND neighbor.project_id = $project_id
              AND neighbor.revision = $revision
            WITH root, collect(DISTINCT neighbor) AS neighbors
            UNWIND ([root] + neighbors) AS item
            RETURN DISTINCT item.canonical_id AS id, item.path AS path
            ORDER BY CASE WHEN path = $path THEN 0 ELSE 1 END, path, id
            LIMIT $limit
        """
        try:
            with self.driver.session() as session:
                rows = session.run(
                    query, tenant_id=tenant_id, project_id=project_id,
                    repository_ref=repository_ref, revision=revision,
                    path=path, limit=limit,
                )
                return [row["id"] for row in rows]
        except (Neo4jError, ServiceUnavailable, SessionExpired) as error:
            raise GraphUnavailable("Memgraph dependency read failed") from error


def project_next(db: Session, graph: GraphProjection) -> str | None:
    """An outbox retry projects current canonical state, so old events cannot resurrect facts."""
    event = db.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.topic.in_(["memory.project", "code.project"]),
            OutboxEvent.status == "pending",
        )
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if event is None:
        return None
    if event.topic == "code.project":
        snapshot = db.get(CodeIndexSnapshot, event.payload.get("snapshot_id"))
        if (
            snapshot is None or snapshot.tenant_id != event.tenant_id
            or snapshot.project_id != event.payload.get("project_id")
        ):
            event.status = "failed"
            event.attempts += 1
            db.commit()
            return event.id
        files = db.scalars(select(CodeFileVersion).where(
            CodeFileVersion.snapshot_id == snapshot.id,
            CodeFileVersion.tenant_id == snapshot.tenant_id,
            CodeFileVersion.project_id == snapshot.project_id,
        ).order_by(CodeFileVersion.path)).all()
        graph.upsert_code_snapshot(snapshot, files)
        event.status = "delivered"
        event.attempts += 1
        db.commit()
        return event.id
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
    snapshots = db.scalars(select(CodeIndexSnapshot).where(
        CodeIndexSnapshot.tenant_id == tenant_id,
        CodeIndexSnapshot.project_id == project_id,
    ).order_by(CodeIndexSnapshot.created_at, CodeIndexSnapshot.id)).all()
    for snapshot in snapshots:
        files = db.scalars(select(CodeFileVersion).where(
            CodeFileVersion.snapshot_id == snapshot.id,
            CodeFileVersion.tenant_id == tenant_id,
            CodeFileVersion.project_id == project_id,
        )).all()
        graph.upsert_code_snapshot(snapshot, files)
    return len(facts) + len(snapshots)


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


def connected_code_lookup(
    db: Session, graph: GraphProjection | None,
    tenant_id: str, project_id: str, repository_ref: str,
    revision: str, query: str, limit: int = 20,
) -> tuple[str, CodeIndexSnapshot | None, list[CodeFileVersion]]:
    """Graph file IDs are hints; canonical revision and hash rows win."""
    snapshot, canonical = code_files_for_revision(
        db, tenant_id, project_id, repository_ref, revision, query, limit
    )
    if snapshot is None:
        return "not_indexed", None, []
    pending = db.scalar(select(OutboxEvent.id).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == "code.project",
        OutboxEvent.status == "pending",
        OutboxEvent.payload["snapshot_id"].as_string() == snapshot.id,
    ).limit(1))
    if graph is None or pending is not None:
        return "canonical_degraded", snapshot, canonical
    try:
        ids = graph.find_file_ids(
            tenant_id, project_id, repository_ref, revision, query, limit
        )
    except GraphUnavailable:
        return "canonical_degraded", snapshot, canonical
    if set(ids) != {file.id for file in canonical}:
        return "canonical_degraded", snapshot, canonical
    return "graph", snapshot, canonical


def connected_dependencies(
    db: Session, graph: GraphProjection | None,
    tenant_id: str, project_id: str, repository_ref: str,
    revision: str, path: str, depth: int = 2, limit: int = 40,
) -> tuple[str, CodeIndexSnapshot | None, list[CodeFileVersion], list[dict[str, str]]]:
    """Bound dependency traversal and recheck the entire result against one commit."""
    if depth not in {1, 2} or not 1 <= limit <= 100 or not 1 <= len(path) <= 500:
        raise ServiceError("CODE_QUERY_INVALID", "Dependency scope is outside policy", 400)
    snapshot = db.scalar(select(CodeIndexSnapshot).where(
        CodeIndexSnapshot.tenant_id == tenant_id,
        CodeIndexSnapshot.project_id == project_id,
        CodeIndexSnapshot.repository_ref == repository_ref,
        CodeIndexSnapshot.commit == revision,
    ))
    if snapshot is None:
        return "not_indexed", None, [], []
    files = db.scalars(select(CodeFileVersion).where(
        CodeFileVersion.snapshot_id == snapshot.id,
        CodeFileVersion.tenant_id == tenant_id,
        CodeFileVersion.project_id == project_id,
    )).all()
    by_path = {file.path: file for file in files}
    if path not in by_path:
        raise ServiceError("CODE_FILE_NOT_FOUND", "File is absent from pinned revision", 404)
    by_id = {file.id: file for file in files}
    all_edges = _resolved_dependencies(files)
    adjacent: dict[str, set[str]] = {file.id: set() for file in files}
    for edge in all_edges:
        adjacent[edge["source_id"]].add(edge["target_id"])
        adjacent[edge["target_id"]].add(edge["source_id"])
    selected = {by_path[path].id}
    frontier = selected.copy()
    for _ in range(depth):
        next_frontier = set()
        for current in frontier:
            next_frontier.update(adjacent[current] - selected)
        selected.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    root = by_path[path]
    canonical = [root] + sorted(
        (by_id[id] for id in selected if id != root.id), key=lambda file: file.path
    )[:limit - 1]
    ids = {file.id for file in canonical}
    edges = [edge for edge in all_edges if (
        edge["source_id"] in ids and edge["target_id"] in ids
    )]
    pending = db.scalar(select(OutboxEvent.id).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.topic == "code.project",
        OutboxEvent.status == "pending",
        OutboxEvent.payload["snapshot_id"].as_string() == snapshot.id,
    ).limit(1))
    if graph is None or pending is not None:
        return "canonical_degraded", snapshot, canonical, edges
    try:
        graph_ids = graph.find_dependency_ids(
            tenant_id, project_id, repository_ref, revision, path, depth, limit
        )
    except GraphUnavailable:
        return "canonical_degraded", snapshot, canonical, edges
    if set(graph_ids) != ids:
        return "canonical_degraded", snapshot, canonical, edges
    return "graph", snapshot, canonical, edges
