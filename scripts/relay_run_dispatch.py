"""Publish pending run dispatch outbox entries to a configured SQS queue."""

from __future__ import annotations

import argparse
import os
import time

import boto3

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.queue_dispatch import publish_run_dispatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 60:
        parser.error("Poll interval must be between 1 and 60 seconds")
    if not settings().database_url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    queue_url = os.environ.get("AIP_RUN_QUEUE_URL", "")
    if not queue_url.startswith("https://"):
        parser.error("AIP_RUN_QUEUE_URL must be an HTTPS SQS queue URL")
    sqs = boto3.client("sqs")
    while True:
        with SessionLocal() as db:
            sent = publish_run_dispatches(db, sqs, queue_url)
        print(f"Published {sent} run dispatch wakeups", flush=True)
        if not args.serve:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
