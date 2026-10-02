"""Back up and add missing paused-run columns to an older development SQLite DB."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from platform_app.config import settings
from platform_app.db import engine

RESUME_COLUMNS = {
    "resume_target": "VARCHAR(40)",
    "resume_key": "VARCHAR(200)",
    "resume_input_hash": "VARCHAR(64)",
}


def main() -> int:
    if settings().environment != "development" or engine.url.drivername != "sqlite":
        raise SystemExit("Only a development SQLite database can be upgraded")
    path = Path(engine.url.database).resolve(strict=True)
    workspace = Path(__file__).resolve().parents[1]
    if not path.is_relative_to(workspace):
        raise SystemExit("Development database is outside this workspace")
    with sqlite3.connect(path, timeout=10) as source:
        columns = {row[1] for row in source.execute("PRAGMA table_info(runs)")}
        if not columns:
            raise SystemExit("Runs table is missing")
        missing = [name for name in RESUME_COLUMNS if name not in columns]
        if not missing:
            print(json.dumps({"status": "current", "added_columns": []}))
            return 0
        backup_root = workspace / "artifacts" / "db-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_root / f"aiplatform-{stamp}.db"
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
        with source:
            for name in missing:
                source.execute(f"ALTER TABLE runs ADD COLUMN {name} {RESUME_COLUMNS[name]}")
        print(
            json.dumps(
                {
                    "status": "upgraded",
                    "added_columns": missing,
                    "backup": str(backup),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
