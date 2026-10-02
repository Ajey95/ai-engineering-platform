"""Revoke expired EC2 sandboxes and reconcile the durable cleanup outbox."""

from __future__ import annotations

import argparse
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from platform_app.config import settings
from platform_app.db import SessionLocal
from platform_app.models import OutboxEvent, SandboxLease
from platform_app.sandbox_broker import (
    due_sandbox_lease_ids,
    revoke_sandbox,
    terminate_revoked_sandbox,
)
from platform_app.sandbox_transport import SandboxObjectKeys, fence_guest_outputs
from platform_app.service import ServiceError


def _pending_ids(db, limit: int = 100) -> list[str]:
    return list(db.scalars(
        select(OutboxEvent.id).where(
            OutboxEvent.topic == "sandbox.cleanup",
            OutboxEvent.status == "pending",
        ).order_by(OutboxEvent.attempts, OutboxEvent.created_at, OutboxEvent.id).limit(limit)
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=20)
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 300:
        parser.error("Poll interval must be between 5 and 300 seconds")
    if not settings().database_url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    ec2 = boto3.client("ec2")
    artifact_bucket = settings().sandbox_artifact_bucket
    s3 = boto3.client("s3") if artifact_bucket else None
    while True:
        incomplete = False
        with SessionLocal() as db:
            due = due_sandbox_lease_ids(db)
        for lease_id in due:
            try:
                with SessionLocal() as db:
                    revoke_sandbox(db, lease_id, "expired")
            except (ServiceError, OSError) as error:
                incomplete = True
                print(f"Sandbox expiry retry: {type(error).__name__}", flush=True)
        with SessionLocal() as db:
            ids = _pending_ids(db)
        if not due and not ids and not args.serve:
            return 0
        for event_id in ids:
            with SessionLocal() as db:
                event = db.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.id == event_id,
                        OutboxEvent.topic == "sandbox.cleanup",
                        OutboxEvent.status == "pending",
                    ).with_for_update(skip_locked=True)
                )
                if event is None:
                    continue
                try:
                    lease = db.get(SandboxLease, event.payload["sandbox_lease_id"])
                    if lease is not None and lease.bootstrap_envelope is not None:
                        if s3 is None:
                            raise ServiceError(
                                "SANDBOX_STORAGE_UNCONFIGURED",
                                "Sandbox artifact bucket is required for cleanup", 503,
                            )
                        keys = SandboxObjectKeys.scoped(
                            lease.tenant_id, lease.project_id, lease.run_id, lease.id
                        )
                        fence_guest_outputs(s3, artifact_bucket, keys)
                    complete = terminate_revoked_sandbox(
                        db, event.payload["sandbox_lease_id"], ec2
                    )
                except (ServiceError, BotoCoreError, ClientError, OSError, KeyError) as error:
                    event.attempts += 1
                    db.commit()
                    incomplete = True
                    print(f"Sandbox cleanup retry: {type(error).__name__}", flush=True)
                else:
                    if complete:
                        event.status = "delivered"
                    else:
                        event.attempts += 1
                        incomplete = True
                    db.commit()
                    print(
                        f"Sandbox cleanup {event.id}: "
                        f"{'complete' if complete else 'termination pending'}",
                        flush=True,
                    )
        if not args.serve:
            if incomplete:
                return 2
            continue
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
