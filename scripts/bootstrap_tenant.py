"""Create the first hosted tenant owner after Alembic migrations.

Run only from a trusted operator shell with AIP_DATABASE_URL set. The subject
must be copied from a verified OIDC identity, never accepted from a browser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.models import AuditEvent, Tenant, TenantMembership


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--owner-subject", required=True)
    args = parser.parse_args()
    if not (1 <= len(args.tenant_id) <= 36 and 2 <= len(args.tenant_name) <= 200):
        parser.error("Tenant ID or name has invalid length")
    if not 1 <= len(args.owner_subject) <= 200:
        parser.error("OIDC subject has invalid length")
    url = os.environ.get("AIP_DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as db, db.begin():
            tenant = db.get(Tenant, args.tenant_id)
            if tenant is None:
                tenant = Tenant(id=args.tenant_id, name=args.tenant_name)
                db.add(tenant)
                db.flush()
            elif tenant.name != args.tenant_name or tenant.status != "active":
                parser.error("Existing tenant name or status does not match")
            existing = db.scalar(select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.subject == args.owner_subject,
            ))
            if existing is None:
                argument_bytes = json.dumps({
                    "tenant_id": tenant.id, "subject": args.owner_subject,
                }, sort_keys=True, separators=(",", ":")).encode()
                db.add(TenantMembership(
                    tenant_id=tenant.id, subject=args.owner_subject, role="owner"
                ))
                db.add(AuditEvent(
                    tenant_id=tenant.id, actor="trusted-operator",
                    action="tenant.bootstrap_owner", target_ref=args.owner_subject,
                    arguments_hash=hashlib.sha256(argument_bytes).hexdigest(),
                    policy_revision=tenant.policy_revision, outcome="allowed",
                ))
            elif existing.role != "owner" or existing.status != "active":
                parser.error("Existing subject is not an active owner")
        print(f"Tenant {args.tenant_id} has an active owner membership")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
