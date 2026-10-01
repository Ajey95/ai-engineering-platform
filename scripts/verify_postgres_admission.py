"""Exercise migrated PostgreSQL admission under concurrent duplicate requests.

Creates and drops one uniquely named local test database. The enabled model
row is a database fixture only; this script never calls a provider.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from platform_app.models import (
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
)
from platform_app.schemas import RunCreate
from platform_app.service import admit_run


def main() -> int:
    database = f"aip_verify_{uuid4().hex[:12]}"
    admin_dsn = "host=127.0.0.1 port=54329 dbname=postgres user=aip password=local_only"
    url = f"postgresql+psycopg://aip:local_only@127.0.0.1:54329/{database}"
    created = False
    engine = None
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            created = True
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env={**os.environ, "AIP_DATABASE_URL": url},
            capture_output=True, text=True, timeout=60,
        )
        if migration.returncode:
            raise RuntimeError(f"Migration failed: {migration.stderr[-1000:]}")
        engine = create_engine(url, pool_pre_ping=True)
        with Session(engine) as session:
            session.add(Tenant(id="fixture-tenant", name="PostgreSQL fixture"))
            session.flush()
            session.add(Project(
                id="fixture-project", tenant_id="fixture-tenant", name="Fixture",
                repository_url="https://example.test/repo.git",
                test_url="http://fixture.test",
                environment_manifest={"named_tests": {"unit": ["pytest", "-q"]}},
            ))
            session.flush()
            session.add_all([
                Task(
                    id="fixture-task", tenant_id="fixture-tenant",
                    project_id="fixture-project", report="Form returns 500 on submit",
                    expected_behavior="Created", actual_behavior="500", created_by="fixture",
                ),
                ModelEntry(
                    id="database-fixture-model", provider="openai", model_id="fixture-only",
                    registry_revision="fixture", state="enabled", context_limit=32000,
                    output_limit=4000, price_revision="fixture",
                    price_per_m_input=Decimal("1"), price_per_m_output=Decimal("2"),
                ),
            ])
            session.commit()

        barrier = Barrier(2)
        body = RunCreate(base_commit="a" * 40, selected_model_entry="database-fixture-model")

        def admit_once() -> str:
            barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    run = admit_run(
                        session, "fixture-tenant", "fixture", "fixture-task",
                        "concurrent-fixture-key", body,
                    )
                    session.commit()
                    return run.id
                except IntegrityError:
                    session.rollback()
                    existing = session.scalar(select(Run).where(
                        Run.tenant_id == "fixture-tenant",
                        Run.created_by == "fixture",
                        Run.idempotency_key == "concurrent-fixture-key",
                    ))
                    if existing is None:
                        raise
                    return existing.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(admit_once) for _ in range(2)]
            ids = [future.result(timeout=30) for future in futures]
        with Session(engine) as session:
            counts = {
                "runs": session.scalar(select(func.count()).select_from(Run)),
                "reservations": session.scalar(select(func.count()).select_from(BudgetEntry)),
                "outbox": session.scalar(select(func.count()).select_from(OutboxEvent)),
                "events": session.scalar(select(func.count()).select_from(RunEvent)),
            }
        result = {
            "schema_revision": "6185524d46e8",
            "same_run_id": ids[0] == ids[1],
            **counts,
            "scope": "synthetic_postgresql_admission_only",
        }
        print(json.dumps(result))
        return 0 if result["same_run_id"] and all(v == 1 for v in counts.values()) else 1
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
                statement = sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
                admin.execute(statement)


if __name__ == "__main__":
    raise SystemExit(main())
