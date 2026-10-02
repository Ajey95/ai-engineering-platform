"""Publish one completed recording to a private S3 bucket under a scoped prefix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from platform_app.config import settings
from platform_app.models import Run
from platform_app.private_media_store import publish_recording
from platform_app.service import ServiceError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--label", required=True, choices=("baseline", "candidate"))
    args = parser.parse_args()
    config = settings()
    if not config.database_url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    if not config.private_media_bucket:
        parser.error("AIP_PRIVATE_MEDIA_BUCKET is required")
    engine = create_engine(config.database_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            run = db.get(Run, args.run_id)
            if run is None:
                parser.error("Run not found")
            try:
                publication = publish_recording(
                    db, config, run, args.label, Path(config.artifact_dir), boto3.client("s3")
                )
            except ServiceError as error:
                parser.error(f"Publication failed: {error.code}")
            db.commit()
            print(json.dumps({
                "run_id": run.id, "label": args.label,
                "publication_id": publication.id, "status": publication.status,
                "effect_hash": publication.effect_hash,
                "object_count": publication.object_count,
            }))
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
