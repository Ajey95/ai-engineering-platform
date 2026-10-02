"""Publish explicitly approved draft PRs from the durable outbox."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.publication_queue import dispatch_one_approved_draft


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if settings().environment == "development":
        parser.error("Hosted publication requires a production environment")
    if not settings().database_url.startswith("postgresql+psycopg://"):
        parser.error("Migrated PostgreSQL is required")
    artifacts = Path(settings().artifact_dir)
    while True:
        event_id = dispatch_one_approved_draft(SessionLocal, artifacts)
        if not args.serve:
            print(f"Publication event processed: {event_id}", flush=True)
            return 0 if event_id else 2
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
