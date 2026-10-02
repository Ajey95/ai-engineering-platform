"""Consume hosted run dispatches after deployment and account qualification."""

from __future__ import annotations

import argparse
import base64
import binascii
from pathlib import Path

import boto3

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.hosted_worker import HostedWorker
from platform_app.queue_consumer import consume_one_run_dispatch
from platform_app.queue_dispatch import publish_run_dispatches
from platform_app.sandbox_broker import SandboxSpec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    config = settings()
    if config.environment == "development":
        parser.error("A hosted environment is required")
    if not config.database_url.startswith("postgresql+psycopg://"):
        parser.error("Migrated PostgreSQL is required")
    queue_url = config.run_queue_url
    if not queue_url.startswith("https://") or not config.sandbox_artifact_bucket:
        parser.error("Hosted SQS URL and sandbox artifact bucket are required")
    try:
        envelope_key = base64.b64decode(
            config.sandbox_envelope_key_b64, validate=True
        )
        spec = SandboxSpec(
            config.sandbox_ami_id,
            config.sandbox_instance_type,
            config.sandbox_subnet_id,
            config.sandbox_security_group_id,
            config.sandbox_root_device_name,
        )
    except (ValueError, binascii.Error) as error:
        parser.error(f"Hosted sandbox settings are invalid: {type(error).__name__}")
    if len(envelope_key) != 32:
        parser.error("Sandbox envelope key must be 32 bytes")
    artifact_root = Path(config.artifact_dir).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    work_root = artifact_root / "_trusted_work"
    work_root.mkdir(parents=True, exist_ok=True)
    sqs = boto3.client("sqs")
    worker = HostedWorker(
        spec, boto3.client("ec2"), boto3.client("s3"),
        config.sandbox_artifact_bucket, envelope_key,
        work_root, artifact_root,
    )
    while True:
        worker.recover_stale()
        with SessionLocal() as db:
            publish_run_dispatches(db, sqs, queue_url)
        completed = consume_one_run_dispatch(
            SessionLocal, sqs, queue_url, worker.process_event
        )
        if not args.serve:
            print(f"Terminal hosted dispatch acknowledged: {completed}", flush=True)
            return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
