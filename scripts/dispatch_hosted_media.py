"""Encode and publish hosted guest recordings from durable media jobs."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import boto3

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.hosted_media_worker import dispatch_one_hosted_media
from platform_app.media_queue import consume_one_media_dispatch, publish_media_dispatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    config = settings()
    if config.environment == "development":
        parser.error("Hosted media requires a production environment")
    if not config.database_url.startswith("postgresql+psycopg://"):
        parser.error("Migrated PostgreSQL is required")
    if not config.private_media_bucket:
        parser.error("A private media bucket is required")
    if not config.media_queue_url.startswith("https://"):
        parser.error("A hosted media SQS queue URL is required")
    root = Path(config.artifact_dir).resolve()
    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    while True:
        with SessionLocal() as db:
            publish_media_dispatches(db, sqs, config.media_queue_url)
        completed = consume_one_media_dispatch(
            SessionLocal, sqs, config.media_queue_url,
            lambda event_id: dispatch_one_hosted_media(
                SessionLocal, config, root, s3, event_id=event_id,
            ),
        )
        if not args.serve:
            print(f"Terminal media dispatch acknowledged: {completed}", flush=True)
            return 0 if completed else 2
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
