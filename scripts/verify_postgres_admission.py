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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from platform_app.models import (
    BudgetEntry,
    ModelEntry,
    OutboxEvent,
    Project,
    ProjectMembership,
    Run,
    RunEvent,
    Task,
    Tenant,
    TenantMembership,
)
from platform_app.run_ledger import claim_run, resume_input_run, transition
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
            capture_output=True,
            text=True,
            timeout=60,
        )
        if migration.returncode:
            raise RuntimeError(f"Migration failed: {migration.stderr[-1000:]}")
        bootstrap = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bootstrap_tenant",
                "--tenant-id",
                "bootstrap-tenant",
                "--tenant-name",
                "Bootstrap fixture",
                "--owner-subject",
                "verified-oidc-subject",
            ],
            env={**os.environ, "AIP_DATABASE_URL": url},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if bootstrap.returncode:
            raise RuntimeError(f"Bootstrap failed: {bootstrap.stderr[-1000:]}")
        engine = create_engine(url, pool_pre_ping=True)
        with Session(engine) as session:
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            bootstrap_owner = (
                session.scalar(
                    select(TenantMembership).where(
                        TenantMembership.tenant_id == "bootstrap-tenant",
                        TenantMembership.subject == "verified-oidc-subject",
                        TenantMembership.role == "owner",
                        TenantMembership.status == "active",
                    )
                )
                is not None
            )
            session.add(Tenant(id="fixture-tenant", name="PostgreSQL fixture"))
            session.flush()
            session.add_all(
                [
                    TenantMembership(tenant_id="fixture-tenant", subject="fixture", role="owner"),
                    ProjectMembership(
                        tenant_id="fixture-tenant",
                        project_id="fixture-project",
                        subject="fixture",
                        role="maintainer",
                    ),
                ]
            )
            session.add(
                Project(
                    id="fixture-project",
                    tenant_id="fixture-tenant",
                    name="Fixture",
                    repository_url="https://example.test/repo.git",
                    test_url="http://fixture.test",
                    environment_manifest={
                        "case_id": "form-submit-001",
                        "named_tests": {"unit": ["pytest", "-q"]},
                    },
                )
            )
            session.flush()
            session.add_all(
                [
                    Task(
                        id="fixture-task",
                        tenant_id="fixture-tenant",
                        project_id="fixture-project",
                        report="Form returns 500 on submit",
                        expected_behavior="Created",
                        actual_behavior="500",
                        created_by="fixture",
                    ),
                    ModelEntry(
                        id="database-fixture-model",
                        provider="openai",
                        model_id="fixture-only",
                        registry_revision="fixture",
                        state="enabled",
                        context_limit=32000,
                        capabilities={"database_fixture_only": True},
                        output_limit=4000,
                        price_revision="fixture",
                        price_per_m_input=Decimal("1"),
                        price_per_m_output=Decimal("2"),
                    ),
                ]
            )
            session.commit()

        with Session(engine) as session:
            session.add(Tenant(id="foreign-tenant", name="Foreign fixture"))
            session.commit()
            session.add(
                ProjectMembership(
                    tenant_id="foreign-tenant",
                    project_id="fixture-project",
                    subject="intruder",
                    role="viewer",
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                cross_tenant_membership_denied = True
            else:
                cross_tenant_membership_denied = False

        barrier = Barrier(2)
        body = RunCreate(
            base_commit="a" * 40,
            selected_model_entry="database-fixture-model",
            reproduction={"fixture_case_id": "form-submit-001"},
        )

        def admit_once() -> str:
            barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    run = admit_run(
                        session,
                        "fixture-tenant",
                        "fixture",
                        "fixture-task",
                        "concurrent-fixture-key",
                        body,
                    )
                    session.commit()
                    return run.id
                except IntegrityError:
                    session.rollback()
                    existing = session.scalar(
                        select(Run).where(
                            Run.tenant_id == "fixture-tenant",
                            Run.created_by == "fixture",
                            Run.idempotency_key == "concurrent-fixture-key",
                        )
                    )
                    if existing is None:
                        raise
                    return existing.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(admit_once) for _ in range(2)]
            ids = [future.result(timeout=30) for future in futures]
        with Session(engine) as session:
            session.add(
                Task(
                    id="intruder-task",
                    tenant_id="foreign-tenant",
                    project_id="fixture-project",
                    report="Cross tenant task",
                    expected_behavior="denied",
                    actual_behavior="attempted",
                    created_by="intruder",
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                cross_tenant_task_denied = True
            else:
                cross_tenant_task_denied = False
            session.add(
                RunEvent(
                    tenant_id="foreign-tenant",
                    run_id=ids[0],
                    sequence=999,
                    event_type="intruder",
                    payload={},
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                cross_tenant_event_denied = True
            else:
                cross_tenant_event_denied = False
        with Session(engine) as session:
            counts = {
                "runs": session.scalar(select(func.count()).select_from(Run)),
                "reservations": session.scalar(select(func.count()).select_from(BudgetEntry)),
                "outbox": session.scalar(select(func.count()).select_from(OutboxEvent)),
                "events": session.scalar(select(func.count()).select_from(RunEvent)),
            }
        # The scoped-key migration is handwritten because Alembic cannot name
        # dropped PostgreSQL constraints reliably during autogeneration.
        engine.dispose()
        engine = None
        for command in ("downgrade", "upgrade"):
            target = "-1" if command == "downgrade" else "head"
            result_command = subprocess.run(
                [sys.executable, "-m", "alembic", command, target],
                env={**os.environ, "AIP_DATABASE_URL": url},
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result_command.returncode:
                raise RuntimeError(f"Migration {command} failed: {result_command.stderr[-1000:]}")
        engine = create_engine(url, pool_pre_ping=True)
        with Session(engine) as session:
            schema_roundtrip = (
                session.scalar(text("SELECT version_num FROM alembic_version")) == revision
            )
            owned, fence = claim_run(session, ids[0], "resume-probe-worker")
            transition(session, owned, "resume-probe-worker", fence, "PREPARING")
            transition(session, owned, "resume-probe-worker", fence, "PAUSED_INPUT")
            session.commit()
            resumed = resume_input_run(
                session,
                "fixture-tenant",
                ids[0],
                "fixture",
                "Use the valid form submission scenario",
                "resume-probe-key-001",
            )
            session.commit()
            dispatches = session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.tenant_id == "fixture-tenant",
                    OutboxEvent.topic == "run.dispatch",
                )
            ).all()
            postgres_resume = resumed.state == "QUEUED" and sorted(
                event.status for event in dispatches
            ) == ["delivered", "pending"]
        result = {
            "schema_revision": revision,
            "schema_roundtrip": schema_roundtrip,
            "postgres_resume": postgres_resume,
            "same_run_id": ids[0] == ids[1],
            "bootstrap_owner": bootstrap_owner,
            "cross_tenant_membership_denied": cross_tenant_membership_denied,
            "cross_tenant_task_denied": cross_tenant_task_denied,
            "cross_tenant_event_denied": cross_tenant_event_denied,
            **counts,
            "scope": "synthetic_postgresql_admission_only",
        }
        print(json.dumps(result))
        return (
            0
            if result["same_run_id"]
            and bootstrap_owner
            and cross_tenant_membership_denied
            and cross_tenant_task_denied
            and cross_tenant_event_denied
            and schema_roundtrip
            and postgres_resume
            and all(v == 1 for v in counts.values())
            else 1
        )
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
