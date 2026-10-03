"""Back up and add missing columns to an older development SQLite DB."""

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
TENANT_COLUMNS = {
    "daily_inference_cap_usd": "NUMERIC(12, 6) NOT NULL DEFAULT 50",
    "monthly_inference_cap_usd": "NUMERIC(12, 6) NOT NULL DEFAULT 500",
    "max_concurrent_runs": "INTEGER NOT NULL DEFAULT 4",
}
MODEL_COLUMNS = {
    "price_per_m_cache_read": "NUMERIC(12, 6)",
    "price_per_m_cache_write": "NUMERIC(12, 6)",
}
ADDITIVE_COLUMNS = {
    "runs": RESUME_COLUMNS, "tenants": TENANT_COLUMNS,
    "model_entries": MODEL_COLUMNS,
}


def main() -> int:
    if settings().environment != "development" or engine.url.drivername != "sqlite":
        raise SystemExit("Only a development SQLite database can be upgraded")
    path = Path(engine.url.database).resolve(strict=True)
    workspace = Path(__file__).resolve().parents[1]
    if not path.is_relative_to(workspace):
        raise SystemExit("Development database is outside this workspace")
    with sqlite3.connect(path, timeout=10) as source:
        missing = {}
        for table, definitions in ADDITIVE_COLUMNS.items():
            columns = {row[1] for row in source.execute(f"PRAGMA table_info({table})")}
            if not columns:
                raise SystemExit(f"{table} table is missing")
            missing[table] = [name for name in definitions if name not in columns]
        if not any(missing.values()):
            print(json.dumps({"status": "current", "added_columns": []}))
            return 0
        backup_root = workspace / "artifacts" / "db-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_root / f"aiplatform-{stamp}.db"
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
        with source:
            for table, names in missing.items():
                for name in names:
                    source.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ADDITIVE_COLUMNS[table][name]}"
                    )
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
