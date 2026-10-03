"""Restore the local fixture PostgreSQL snapshot into a disposable database.

This Windows/WSL drill never replaces the active database and never serves
the restored database. It does not qualify a hosted backup or RPO/RTO.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.models import PrivateMediaPublication, RecordingDeletion
from platform_app.recording_deletion import reconcile_local_recording_deletions

_HOST = "127.0.0.1"
_PORT = 54329
_USER = "aip"
_PASSWORD = "local_only"  # Dedicated local Compose fixture, never a hosted credential.
_SOURCE = "aip"
_CONTAINER = "aiplatform-postgres-1"


def _docker(*args: str, input_bytes: bytes | None = None) -> bytes:
    command = [
        "wsl.exe", "-d", "Ubuntu-24.04", "-u", "root", "--",
        "docker", "exec", *(["-i"] if input_bytes is not None else []),
        _CONTAINER, *args,
    ]
    result = subprocess.run(
        command, input=input_bytes, capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(
            f"Local PostgreSQL container command failed: {result.stderr[-1000:]!r}"
        )
    return result.stdout


def _dsn(database: str) -> str:
    return (
        f"host={_HOST} port={_PORT} dbname={database} "
        f"user={_USER} password={_PASSWORD}"
    )


def _counts(connection: psycopg.Connection) -> dict[str, int]:
    tables = [row[0] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )]
    return {
        table: connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()[0]
        for table in tables
    }


def _sequence_mismatches(connection: psycopg.Connection) -> int:
    return connection.execute("""
        SELECT count(*) FROM runs r WHERE r.last_sequence != COALESCE((
            SELECT max(e.sequence) FROM run_events e
            WHERE e.tenant_id = r.tenant_id AND e.run_id = r.id
        ), 0)
    """).fetchone()[0]


def _unvalidated_foreign_keys(connection: psycopg.Connection) -> int:
    return connection.execute("""
        SELECT count(*) FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'public'::regnamespace
        AND NOT convalidated
    """).fetchone()[0]


def _replay_local_tombstones(database: str, workspace: Path) -> dict:
    url = f"postgresql+psycopg://{_USER}:{_PASSWORD}@{_HOST}:{_PORT}/{database}"
    engine = create_engine(url, pool_pre_ping=True)
    try:
        work = (workspace / "artifacts" / "test-tmp").resolve()
        work.mkdir(parents=True, exist_ok=True)
        if not work.is_relative_to(workspace.resolve()):
            raise RuntimeError("Restore probe directory escaped the workspace")
        with TemporaryDirectory(prefix="aip-restore-replay-", dir=work) as temporary:
            root = Path(temporary).resolve()
            if not root.is_relative_to(workspace.resolve()):
                raise RuntimeError("Restore replay target escaped the workspace")
            planted = []
            with Session(engine) as db:
                deletions = db.scalars(select(RecordingDeletion)).all()
                for deletion in deletions:
                    if (
                        not re.fullmatch(r"[A-Za-z0-9-]{1,64}", deletion.tenant_id)
                        or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", deletion.run_id)
                        or deletion.label not in {"baseline", "candidate"}
                    ):
                        raise RuntimeError("Restored tombstone has an unsafe path scope")
                    remote_ready = db.scalar(select(PrivateMediaPublication.id).where(
                        PrivateMediaPublication.tenant_id == deletion.tenant_id,
                        PrivateMediaPublication.run_id == deletion.run_id,
                        PrivateMediaPublication.label == deletion.label,
                        PrivateMediaPublication.status == "ready",
                    ))
                    if remote_ready is not None:
                        continue
                    media = (
                        root / "private-media" / deletion.tenant_id
                        / f"{deletion.run_id}_{deletion.label}" / "media" / "synthetic.bin"
                    )
                    media.parent.mkdir(parents=True, exist_ok=True)
                    media.write_bytes(b"deleted-on-restore")
                    planted.append(media)
            outcome = reconcile_local_recording_deletions(
                lambda: Session(engine), str(root)
            )
            if outcome["failed"] or outcome["cleaned"] != len(planted):
                raise RuntimeError("Restored tombstones did not reconcile")
            if any(path.exists() for path in planted):
                raise RuntimeError("Restored media survived deletion replay")
            return {"tombstones_checked": outcome["checked"],
                    "synthetic_media_removed": len(planted)}
    finally:
        engine.dispose()


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    database = f"aip_restore_{uuid4().hex[:12]}"
    if not re.fullmatch(r"aip_restore_[0-9a-f]{12}", database):
        raise RuntimeError("Disposable restore database name is invalid")
    started = time.monotonic()
    created = False
    try:
        with psycopg.connect(_dsn("postgres"), autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(database)
            ))
            created = True
        with psycopg.connect(_dsn(_SOURCE), autocommit=True, connect_timeout=5) as source:
            source.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            counts = _counts(source)
            revision = source.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            sequence_mismatches = _sequence_mismatches(source)
            dump = _docker(
                "pg_dump", "-Fc", "--no-owner", "--no-privileges",
                f"--snapshot={snapshot}", "-U", _USER, "-d", _SOURCE,
            )
        if not dump:
            raise RuntimeError("Local PostgreSQL dump is empty")
        _docker(
            "pg_restore", "--exit-on-error", "--single-transaction",
            "--no-owner", "--no-privileges", "-U", _USER, "-d", database,
            input_bytes=dump,
        )
        restore_seconds = round(time.monotonic() - started, 2)
        with psycopg.connect(_dsn(database), connect_timeout=5) as restored:
            if _counts(restored) != counts:
                raise RuntimeError("Restored table counts differ from dump snapshot")
            if restored.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] != revision:
                raise RuntimeError("Restored schema revision differs")
            if _sequence_mismatches(restored) != sequence_mismatches:
                raise RuntimeError("Restored event sequences differ")
            if _unvalidated_foreign_keys(restored):
                raise RuntimeError("Restored foreign keys are unvalidated")
        drift = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=workspace, capture_output=True, text=True, timeout=60,
            env={**os.environ, "AIP_DATABASE_URL": (
                f"postgresql+psycopg://{_USER}:{_PASSWORD}@{_HOST}:{_PORT}/{database}"
            )},
        )
        if drift.returncode:
            raise RuntimeError(f"Restored schema drift check failed: {drift.stderr[-1000:]}")
        replay = _replay_local_tombstones(database, workspace)
        result = {
            "scope": "isolated_local_postgresql_fixture_only",
            "schema_revision": revision, "table_count": len(counts),
            "dump_bytes": len(dump), "dump_sha256": hashlib.sha256(dump).hexdigest(),
            "local_restore_seconds": restore_seconds,
            "run_event_sequence_mismatches": sequence_mismatches,
            **replay,
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        if created:
            with psycopg.connect(_dsn("postgres"), autocommit=True, connect_timeout=5) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                ))


if __name__ == "__main__":
    sys.exit(main())
