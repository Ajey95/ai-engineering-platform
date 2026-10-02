"""Consume SQS wakeups for the synthetic development fixture worker only."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.development_worker import DevelopmentWorker
from platform_app.queue_consumer import consume_one_run_dispatch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path, default=Path(settings().artifact_dir))
    parser.add_argument("--runtime", choices=["native", "wsl"], default="wsl")
    parser.add_argument("--image", default="aip-dev-sandbox:0.1.1")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if settings().environment != "development":
        parser.error("This consumer executes synthetic development fixtures only")
    if not settings().database_url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    queue_url = os.environ.get("AIP_RUN_QUEUE_URL", "")
    if not queue_url.startswith("https://"):
        parser.error("AIP_RUN_QUEUE_URL must be an HTTPS SQS queue URL")
    worker = DevelopmentWorker(
        args.repository, args.artifacts, runtime=args.runtime, image=args.image
    )
    sqs = boto3.client("sqs")
    while True:
        completed = consume_one_run_dispatch(
            SessionLocal, sqs, queue_url, worker.process_next
        )
        if not args.serve:
            print(f"Terminal dispatch acknowledged: {completed}", flush=True)
            return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
