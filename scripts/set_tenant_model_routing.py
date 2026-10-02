"""Set a tenant's automatic model routing allowlist from a trusted operator shell."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.model_routing import RoutingError, validate_routing_policy
from platform_app.models import AuditEvent, ModelEntry, Tenant
from platform_app.service import canonical_hash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--policy-file", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= len(args.operator) <= 200:
        parser.error("Operator identity must be 1 to 200 characters")
    url = os.environ.get("AIP_DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    try:
        policy = json.loads(args.policy_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        parser.error(f"Policy file is unreadable: {type(error).__name__}")
    if policy == {"enabled": False}:
        allowed_ids: set[str] = set()
    else:
        try:
            allowed_ids, _, _, _ = validate_routing_policy(policy)
        except RoutingError as error:
            parser.error(f"Invalid routing policy: {error.code}")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as db, db.begin():
            tenant = db.scalar(
                select(Tenant).where(Tenant.id == args.tenant_id).with_for_update()
            )
            if tenant is None:
                parser.error("Tenant not found")
            found = set(db.scalars(select(ModelEntry.id).where(ModelEntry.id.in_(allowed_ids))))
            if found != allowed_ids:
                parser.error("Policy references an unregistered model entry")
            before = tenant.model_routing_policy or {}
            if before != policy:
                tenant.model_routing_policy = policy
                db.add(AuditEvent(
                    tenant_id=tenant.id,
                    actor=args.operator,
                    action="tenant.model_routing.update",
                    target_ref=tenant.id,
                    arguments_hash=canonical_hash({"before": before, "after": policy}),
                    policy_revision=tenant.policy_revision,
                    outcome="allowed",
                ))
        print(f"Tenant {args.tenant_id} model routing policy recorded")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
