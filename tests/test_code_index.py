"""Exact-commit code indexing, scope and degraded retrieval contracts."""

import hashlib
import io
import tarfile
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api
from platform_app.code_index import code_files_for_revision, index_source_archive
from platform_app.db import Base
from platform_app.graph_memory import (
    GraphUnavailable,
    _resolved_dependencies,
    connected_code_lookup,
    project_next,
)
from platform_app.models import (
    CodeFileVersion,
    CodeIndexSnapshot,
    ModelEntry,
    OutboxEvent,
    Project,
    Run,
    Task,
    Tenant,
)
from platform_app.repository_archive import SourceArchive


def source(commit: str, files: dict[str, bytes]) -> SourceArchive:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    raw = output.getvalue()
    return SourceArchive(commit, hashlib.sha256(raw).hexdigest(), raw, len(files))


class CodeGraph:
    def __init__(self):
        self.files = {}
        self.fail = False

    def upsert_code_snapshot(self, snapshot, files):
        if self.fail:
            raise GraphUnavailable("offline")
        self.files[snapshot.commit] = list(files)

    def find_file_ids(self, tenant_id, project_id, repository_ref, revision, query, limit):
        if self.fail:
            raise GraphUnavailable("offline")
        return [
            file.id for file in self.files.get(revision, [])
            if file.tenant_id == tenant_id and file.project_id == project_id
            and (query in file.path or any(query in symbol["qualified_name"]
                                           for symbol in file.symbols))
        ][:limit]


def test_index_versions_scope_dependencies_and_outbox(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Tenant(id="tenant-a", name="A"),
            Tenant(id="tenant-b", name="B"),
            Project(
                id="project-a", tenant_id="tenant-a", name="A",
                repository_url="https://github.com/example/repo",
            ),
            Task(
                id="task-a", tenant_id="tenant-a", project_id="project-a",
                report="bug", expected_behavior="works", actual_behavior="fails",
                created_by="actor",
            ),
            ModelEntry(
                id="model-a", provider="openai", model_id="verified-model",
                registry_revision="r1", state="enabled", capabilities={},
                validated_at=datetime.now(UTC), context_limit=10000,
                output_limit=1000, price_revision="p1",
                price_per_m_input=Decimal("1"), price_per_m_output=Decimal("1"),
            ),
        ])
        db.commit()
        older = source("a" * 40, {
            "app.py": b"from helper import answer\nclass Handler:\n    pass\n",
            "helper.py": b"def answer():\n    return 1\n",
            "web/app.ts": b"import { x } from './helper';\nfunction render() {}\n",
            "web/helper.ts": b"export const x = 1;\n",
        })
        newer = source("b" * 40, {
            "app.py": b"def fixed():\n    return 2\n",
        })
        for ordinal, item in enumerate((older, newer)):
            run = Run(
                    id=f"run-{ordinal}", tenant_id="tenant-a", project_id="project-a",
                    task_id="task-a", created_by="actor", idempotency_key=f"key-{ordinal}",
                    request_hash="c" * 64, base_commit=item.commit,
                    model_entry_id="model-a",
                state="QUEUED", config_snapshot={
                    "repository_url": "https://github.com/example/repo",
                },
            )
            db.add(run)
            db.flush()
            indexed = index_source_archive(db, run, item, tmp_path)
            assert indexed.indexed_files == item.file_count
            assert index_source_archive(db, run, item, tmp_path).id == indexed.id
            db.commit()
        assert db.scalar(select(CodeIndexSnapshot).where(
            CodeIndexSnapshot.tenant_id == "tenant-a"
        ).order_by(CodeIndexSnapshot.commit)).commit == older.commit
        assert len(db.scalars(select(OutboxEvent).where(
            OutboxEvent.topic == "code.project"
        )).all()) == 2
        snapshot, old_matches = code_files_for_revision(
            db, "tenant-a", "project-a", "example/repo", older.commit, "answer"
        )
        assert snapshot is not None
        assert {file.path for file in old_matches} == {"helper.py"}
        assert code_files_for_revision(
            db, "tenant-a", "project-a", "example/repo", newer.commit, "answer"
        )[1] == []
        assert code_files_for_revision(
            db, "tenant-b", "project-a", "example/repo", older.commit, "answer"
        ) == (None, [])
        assert code_files_for_revision(
            db, "tenant-a", "project-a", "other/repo", older.commit, "answer"
        ) == (None, [])
        files = db.scalars(select(CodeFileVersion).where(
            CodeFileVersion.snapshot_id == snapshot.id
        )).all()
        paths = {file.id: file.path for file in files}
        edges = {(paths[edge["source_id"]], paths[edge["target_id"]])
                 for edge in _resolved_dependencies(files)}
        assert ("app.py", "helper.py") in edges
        assert ("web/app.ts", "web/helper.ts") in edges

        graph = CodeGraph()
        assert connected_code_lookup(
            db, graph, "tenant-a", "project-a", "example/repo", older.commit, "answer"
        )[0] == "canonical_degraded"
        while project_next(db, graph) is not None:
            pass
        assert connected_code_lookup(
            db, graph, "tenant-a", "project-a", "example/repo", older.commit, "answer"
        )[0] == "graph"
        graph.fail = True
        assert connected_code_lookup(
            db, graph, "tenant-a", "project-a", "example/repo", older.commit, "answer"
        )[0] == "canonical_degraded"

        api.app.dependency_overrides[api.db_session] = lambda: db
        api.app.dependency_overrides[api.principal] = lambda: ("tenant-a", "actor")
        try:
            response = TestClient(api.app).get(
                f"/v1/projects/project-a/code-index?source_revision={older.commit}&query=answer"
            )
            assert response.status_code == 200
            assert [file["path"] for file in response.json()["files"]] == ["helper.py"]
            api.app.dependency_overrides[api.principal] = lambda: ("tenant-b", "actor")
            assert TestClient(api.app).get(
                f"/v1/projects/project-a/code-index?source_revision={older.commit}&query=answer"
            ).status_code == 404
        finally:
            api.app.dependency_overrides.clear()
    engine.dispose()
