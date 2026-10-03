"""Exercise migrated PostgreSQL admission, resume and recording deletion races.

Creates and drops one uniquely named local test database. The enabled model
row is a database fixture only; this script never calls a provider.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from platform_app.export_quota import reserve_export
from platform_app.media_quota import media_usage_seconds, reserve_media_attempt
from platform_app.models import (
    AuditEvent,
    BudgetEntry,
    ExportCharge,
    MediaMinuteCharge,
    ModelEntry,
    OutboxEvent,
    Project,
    ProjectMembership,
    RecordingDeletion,
    Run,
    RunEvent,
    SandboxLease,
    Task,
    Tenant,
    TenantMembership,
    ToolAction,
)
from platform_app.run_ledger import claim_run, resume_input_run, transition
from platform_app.sandbox_broker import SandboxSpec, reserve_sandbox
from platform_app.sandbox_quota import sandbox_usage_seconds
from platform_app.schemas import RunCreate
from platform_app.service import ServiceError, admit_run
from platform_app.tenant_quota import QuotaError, check_inference_reservation, lock_tenant


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
        operator_command = [
            sys.executable, "-m", "scripts.set_tenant_quotas",
            "--tenant-id", "fixture-tenant",
            "--daily-inference-cap-usd", "0.015",
            "--monthly-inference-cap-usd", "0.015",
            "--max-concurrent-runs", "2",
            "--daily-export-cap-bytes", "100",
            "--daily-sandbox-minutes", "30",
            "--daily-media-minutes", "2",
        ]
        operator_result = subprocess.run(
            operator_command, env={**os.environ, "AIP_DATABASE_URL": url},
            capture_output=True, text=True, timeout=30,
        )
        if operator_result.returncode:
            raise RuntimeError(f"Tenant quota update failed: {operator_result.stderr[-1000:]}")
        operator_retry = subprocess.run(
            operator_command, env={**os.environ, "AIP_DATABASE_URL": url},
            capture_output=True, text=True, timeout=30,
        )
        if operator_retry.returncode:
            raise RuntimeError(f"Tenant quota retry failed: {operator_retry.stderr[-1000:]}")
        with Session(engine) as session:
            tenant = session.get(Tenant, "fixture-tenant")
            quota_operator_audit = (
                tenant.max_concurrent_runs == 2
                and tenant.daily_export_cap_bytes == 100
                and tenant.daily_sandbox_minutes == 30
                and tenant.daily_media_minutes == 2
                and Decimal(tenant.daily_inference_cap_usd) == Decimal("0.015")
                and session.scalar(select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.tenant_id == tenant.id,
                    AuditEvent.action == "tenant.quotas.update",
                )) == 1
            )

        admission_barrier = Barrier(2)

        def admit_distinct(index: int) -> str:
            admission_barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    admitted = admit_run(
                        session, "fixture-tenant", "fixture", "fixture-task",
                        f"quota-race-{index}", body,
                    )
                    session.commit()
                    return admitted.id
                except ServiceError as error:
                    session.rollback()
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(admit_distinct, index) for index in range(2)]
            admission_results = [future.result(timeout=30) for future in futures]
        postgres_run_quota_race = (
            admission_results.count("RUN_CONCURRENCY_EXHAUSTED") == 1
            and len([value for value in admission_results if value != "RUN_CONCURRENCY_EXHAUSTED"])
            == 1
        )

        reservation_barrier = Barrier(2)

        def reserve_tenant(index: int) -> str:
            reservation_barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    tenant = lock_tenant(session, "fixture-tenant")
                    check_inference_reservation(session, tenant, Decimal("0.012"))
                    session.add(BudgetEntry(
                        tenant_id=tenant.id, run_id=ids[0], category=f"call:race-{index}",
                        reserved_usd=Decimal("0.012"), actual_usd=Decimal(0),
                        status="reserved",
                    ))
                    session.commit()
                    return "reserved"
                except QuotaError as error:
                    session.rollback()
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve_tenant, index) for index in range(2)]
            reservation_results = [future.result(timeout=30) for future in futures]
        postgres_inference_quota_race = sorted(reservation_results) == [
            "TENANT_BUDGET_EXHAUSTED", "reserved"
        ]
        export_barrier = Barrier(2)

        def export_tenant(index: int) -> str:
            export_barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    reserve_export(
                        session, "fixture-tenant", ids[0], "fixture", 60,
                        f"{index}" * 64,
                    )
                    session.commit()
                    return "exported"
                except QuotaError as error:
                    session.rollback()
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(export_tenant, index) for index in range(2)]
            export_results = [future.result(timeout=30) for future in futures]
        with Session(engine) as session:
            export_rows = session.scalar(select(func.count(ExportCharge.id)).where(
                ExportCharge.tenant_id == "fixture-tenant",
            ))
        postgres_export_quota_race = sorted(export_results) == [
            "EXPORT_QUOTA_EXHAUSTED", "exported"
        ] and export_rows == 1
        with Session(engine) as session:
            for index in range(2):
                session.add(Run(
                    id=f"sandbox-race-{index}", tenant_id="fixture-tenant",
                    project_id="fixture-project", task_id="fixture-task",
                    created_by="fixture", idempotency_key=f"sandbox-race-{index}",
                    request_hash="a" * 64, base_commit="a" * 40,
                    model_entry_id="database-fixture-model", state="PREPARING",
                    lease_owner="quota-worker", lease_fence=1,
                    lease_until=datetime.now(UTC) + timedelta(minutes=5),
                    config_snapshot={},
                ))
            session.commit()
        sandbox_barrier = Barrier(2)
        sandbox_spec = SandboxSpec(
            "ami-12345678", "m6i.large", "subnet-12345678",
            "sg-12345678", "/dev/xvda",
        )

        def reserve_guest(index: int) -> str:
            sandbox_barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    reserve_sandbox(
                        session, f"sandbox-race-{index}", "quota-worker", 1,
                        sandbox_spec,
                    )
                    return "reserved"
                except ServiceError as error:
                    session.rollback()
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve_guest, index) for index in range(2)]
            sandbox_results = [future.result(timeout=30) for future in futures]
        with Session(engine) as session:
            sandbox_rows = session.scalar(select(func.count(SandboxLease.id)).where(
                SandboxLease.tenant_id == "fixture-tenant",
            ))
            sandbox_seconds = sandbox_usage_seconds(
                session, "fixture-tenant", now=datetime.now(UTC),
            )
        postgres_sandbox_quota_race = sorted(sandbox_results) == [
            "SANDBOX_QUOTA_EXHAUSTED", "reserved",
        ] and sandbox_rows == 1 and sandbox_seconds == 1800
        media_barrier = Barrier(2)

        def reserve_media(index: int) -> str:
            media_barrier.wait(timeout=10)
            with Session(engine) as session:
                try:
                    reserve_media_attempt(
                        session, f"sandbox-race-{index}", "baseline", 1,
                        f"{index}" * 64, 70.0,
                    )
                    session.commit()
                    return "reserved"
                except ServiceError as error:
                    session.rollback()
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve_media, index) for index in range(2)]
            media_results = [future.result(timeout=30) for future in futures]
        with Session(engine) as session:
            media_rows = session.scalar(select(func.count(MediaMinuteCharge.id)).where(
                MediaMinuteCharge.tenant_id == "fixture-tenant",
            ))
            media_seconds = media_usage_seconds(
                session, "fixture-tenant", now=datetime.now(UTC),
            )
        postgres_media_quota_race = sorted(media_results) == [
            "MEDIA_QUOTA_EXHAUSTED", "reserved",
        ] and media_rows == 1 and media_seconds == 70
        # Reproduce the PostgreSQL lock boundary at the actual deletion handler.
        # Both requests must return successfully while only one final event exists.
        import platform_app.api as api_module
        from platform_app.api import delete_run_recording

        deletion_run_id = str(uuid4())
        with Session(engine) as session:
            session.add(Run(
                id=deletion_run_id,
                tenant_id="fixture-tenant",
                project_id="fixture-project",
                task_id="fixture-task",
                created_by="fixture",
                idempotency_key="recording-delete-race",
                request_hash="a" * 64,
                base_commit="b" * 40,
                model_entry_id="database-fixture-model",
                state="COMPLETED",
                media_status="READY",
                config_snapshot={},
            ))
            session.add(ToolAction(
                tenant_id="fixture-tenant",
                run_id=deletion_run_id,
                step_id="browser",
                logical_action="fixture.browser",
                effect_key="c" * 64,
                arguments_hash="d" * 64,
                policy_result="allowed",
                status="COMPLETED",
                receipt={"recording": "recording.webm"},
            ))
            session.commit()
        artifact_base = Path(__file__).resolve().parents[1] / "artifacts"
        artifact_base.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="aip-delete-race-", dir=artifact_base) as directory:
            root = Path(directory)
            media = (
                root / "private-media" / "fixture-tenant"
                / f"{deletion_run_id}_baseline" / "media" / ("e" * 64)
            )
            media.mkdir(parents=True)
            (media / "master.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            evidence = root / deletion_run_id / "baseline"
            evidence.mkdir(parents=True)
            (evidence / "recording.webm").write_bytes(b"synthetic recording")
            (evidence / "final.png").write_bytes(b"synthetic screenshot")
            deletion_barrier = Barrier(2)

            def delete_once() -> dict:
                with Session(engine) as session:
                    deletion_barrier.wait(timeout=10)
                    return delete_run_recording(
                        deletion_run_id, "baseline", ("fixture-tenant", "fixture"), session
                    )

            with patch.object(api_module, "settings", lambda: SimpleNamespace(
                environment="development", artifact_dir=str(root)
            )):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(delete_once) for _ in range(2)]
                    deleted = [future.result(timeout=30) for future in futures]
            with Session(engine) as session:
                deletion_rows = session.scalars(select(RecordingDeletion).where(
                    RecordingDeletion.run_id == deletion_run_id
                )).all()
                deletion_events = session.scalars(select(RunEvent).where(
                    RunEvent.run_id == deletion_run_id,
                    RunEvent.event_type == "artifact.deleted",
                )).all()
            postgres_recording_delete_race = (
                all(item["status"] == "complete" for item in deleted)
                and len(deletion_rows) == 1
                and deletion_rows[0].status == "complete"
                and len(deletion_events) == 1
                and not media.exists()
                and not (evidence / "recording.webm").exists()
                and (evidence / "final.png").is_file()
            )
        result = {
            "schema_revision": revision,
            "schema_roundtrip": schema_roundtrip,
            "postgres_resume": postgres_resume,
            "postgres_recording_delete_race": postgres_recording_delete_race,
            "postgres_run_quota_race": postgres_run_quota_race,
            "postgres_inference_quota_race": postgres_inference_quota_race,
            "postgres_export_quota_race": postgres_export_quota_race,
            "postgres_sandbox_quota_race": postgres_sandbox_quota_race,
            "postgres_media_quota_race": postgres_media_quota_race,
            "quota_operator_audit": quota_operator_audit,
            "same_run_id": ids[0] == ids[1],
            "bootstrap_owner": bootstrap_owner,
            "cross_tenant_membership_denied": cross_tenant_membership_denied,
            "cross_tenant_task_denied": cross_tenant_task_denied,
            "cross_tenant_event_denied": cross_tenant_event_denied,
            **counts,
            "scope": "synthetic_postgresql_integration_only",
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
            and postgres_recording_delete_race
            and postgres_run_quota_race
            and postgres_inference_quota_race
            and postgres_export_quota_race
            and postgres_sandbox_quota_race
            and postgres_media_quota_race
            and quota_operator_audit
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
