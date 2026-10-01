from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base
from platform_app.development_worker import DevelopmentWorker, _pinned_fixture
from platform_app.models import OutboxEvent, Run, Tenant
from platform_app.service import ServiceError


def test_pinned_fixture_contains_baseline_and_oracle(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    import subprocess

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    manifest, workspace, oracle = _pinned_fixture(repository, commit, tmp_path)
    assert manifest.is_file()
    assert (workspace / "server.py").is_file()
    assert oracle.is_file()
    assert not oracle.is_relative_to(workspace)


def test_untrusted_fixture_revision_is_rejected(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    with pytest.raises(ServiceError) as error:
        _pinned_fixture(repository, "0" * 40, tmp_path)
    assert error.value.code == "FIXTURE_UNAVAILABLE"


def test_development_worker_does_not_consume_other_dispatches(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(
            Run(
                id="run-a",
                tenant_id="tenant-a",
                task_id="task-a",
                project_id="project-a",
                created_by="alice",
                idempotency_key="key-a",
                request_hash="hash",
                base_commit="a" * 40,
                model_entry_id="model-a",
                state="QUEUED",
                config_snapshot={"reproduction": {}},
            )
        )
        db.add(
            OutboxEvent(
                id="event-a",
                tenant_id="tenant-a",
                topic="run.dispatch",
                payload={"run_id": "run-a"},
            )
        )
        db.commit()
    worker = DevelopmentWorker(
        Path(__file__).resolve().parents[1], tmp_path, session_factory=factory
    )
    assert worker.process_next() is None
    with factory() as db:
        assert db.scalar(select(OutboxEvent.status)) == "pending"
    engine.dispose()
