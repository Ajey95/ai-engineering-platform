"""Evaluate durable operational alerts from canonical PostgreSQL records."""

from __future__ import annotations

import argparse
import time

from sqlalchemy import select

from platform_app.db import SessionLocal
from platform_app.models import Tenant
from platform_app.operational_alerts import evaluate_tenant_alerts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve", action="store_true", help="Continuously sample tenant conditions"
    )
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--tenant", help="Evaluate one tenant only")
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 60:
        parser.error("Poll interval must be between 5 and 60 seconds")
    while True:
        with SessionLocal() as db:
            tenant_ids = [args.tenant] if args.tenant else db.scalars(select(Tenant.id).where(
                Tenant.status == "active",
            )).all()
        fired = 0
        for tenant_id in tenant_ids:
            with SessionLocal() as db:
                alerts = evaluate_tenant_alerts(db, tenant_id)
                fired += sum(item.state == "firing" for item in alerts)
                db.commit()
        print(f"Evaluated {len(tenant_ids)} tenants; {fired} firing alerts", flush=True)
        if not args.serve:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
