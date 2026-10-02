import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import platform_app.repository_fetch as repository_fetch
from platform_app.db import Base
from platform_app.models import Project, RepositoryConnection, Run, Task, Tenant
from platform_app.repository_fetch import (
    RepositoryFetchError,
    _fetch_archive,
    fetch_authorized_run_source,
    fetch_pinned_github_archive,
)


def _run(directory, *args):
    result = subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True,
        check=True,
    )
    return result.stdout.strip()


def test_fetch_exact_commit_excludes_dirty_or_untracked_files(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _run(origin, "init", "--quiet")
    _run(origin, "config", "user.name", "Fixture")
    _run(origin, "config", "user.email", "fixture@example.test")
    _run(origin, "config", "core.autocrlf", "false")
    (origin / "app.py").write_bytes(b"print('pinned')\n")
    _run(origin, "add", "app.py")
    _run(origin, "commit", "--quiet", "-m", "pinned")
    commit = _run(origin, "rev-parse", "HEAD")
    (origin / "app.py").write_bytes(b"print('dirty')\n")
    (origin / "secret.env").write_bytes(b"never archive")
    work_root = tmp_path / "worker"
    work_root.mkdir()
    archive = _fetch_archive(str(origin), commit, work_root, authorization=None)
    assert archive.commit == commit
    assert b"pinned" in archive.archive
    assert b"dirty" not in archive.archive
    assert b"secret.env" not in archive.archive
    assert list(work_root.iterdir()) == []


def test_fetch_rejects_invalid_scope_before_network(tmp_path):
    with pytest.raises(RepositoryFetchError):
        fetch_pinned_github_archive("owner/repo", "not-a-sha", "token", tmp_path)
    with pytest.raises((RepositoryFetchError, ValueError)):
        fetch_pinned_github_archive("owner/../repo", "a" * 40, "token", tmp_path)


def test_run_source_requires_scoped_ready_connection_and_env_secret(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        db.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="bug", expected_behavior="works", actual_behavior="broken",
            created_by="alice",
        ))
        db.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a",
            task_id="task-a", created_by="alice", idempotency_key="key-a",
            request_hash="a" * 64, base_commit="b" * 40,
            model_entry_id="model-a", state="PREPARING",
            config_snapshot={"repository_url": "https://github.com/owner/repo"},
        ))
        db.commit()
        with pytest.raises(RepositoryFetchError, match="ready"):
            fetch_authorized_run_source(db, "run-a", tmp_path)
        connection = RepositoryConnection(
            id="connection-a", tenant_id="tenant-a", project_id="project-a",
            provider="github", repository_ref="owner/repo",
            credential_ref="secret://env/AIP_GITHUB_TOKEN", status="ready",
            created_by="alice",
        )
        db.add(connection)
        db.commit()
        observed = []
        monkeypatch.setenv("AIP_GITHUB_TOKEN", "fixture-token")
        monkeypatch.setattr(repository_fetch, "fetch_pinned_github_archive",
                            lambda *args: observed.append(args) or "archive")
        assert fetch_authorized_run_source(db, "run-a", tmp_path) == "archive"
        assert observed == [("owner/repo", "b" * 40, "fixture-token", tmp_path)]
        connection.status = "disabled"
        db.commit()
        with pytest.raises(RepositoryFetchError, match="ready"):
            fetch_authorized_run_source(db, "run-a", tmp_path)
    engine.dispose()
