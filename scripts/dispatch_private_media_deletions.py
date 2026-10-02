"""Reconcile revoked S3 recordings and CloudFront invalidations from the outbox."""

from __future__ import annotations

import argparse
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.models import OutboxEvent
from platform_app.private_media_deletion import process_private_media_deletion
from platform_app.service import ServiceError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30)
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 300:
        parser.error("Poll interval must be between 5 and 300 seconds")
    config = settings()
    if not config.database_url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    if not config.private_media_bucket or not config.cloudfront_distribution_id:
        parser.error("Private media bucket and CloudFront distribution are required")
    s3 = boto3.client("s3")
    cloudfront = boto3.client("cloudfront")
    while True:
        with SessionLocal() as db:
            event = db.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "private_media.delete",
                    OutboxEvent.status == "pending",
                ).order_by(OutboxEvent.created_at).with_for_update(skip_locked=True).limit(1)
            )
            if event is None:
                if not args.serve:
                    return 0
            else:
                try:
                    complete = process_private_media_deletion(
                        db, config, event, s3, cloudfront
                    )
                except (ServiceError, BotoCoreError, ClientError, OSError, KeyError) as error:
                    event.attempts += 1
                    db.commit()
                    print(f"Private media deletion retry: {type(error).__name__}", flush=True)
                    if not args.serve:
                        return 2
                else:
                    db.commit()
                    print(
                        f"Private media deletion {event.id}: "
                        f"{'complete' if complete else 'edge pending'}",
                        flush=True,
                    )
                    if not args.serve and not complete:
                        return 2
        if not args.serve:
            continue
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
