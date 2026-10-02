"""Set tenant inference and run quotas from a trusted PostgreSQL operator shell."""

from __future__ import annotations

import argparse
import os
from decimal import Decimal, InvalidOperation

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from platform_app.models import AuditEvent, Tenant
from platform_app.service import canonical_hash


def _money(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("A decimal dollar amount is required") from error
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999.999999"):
        raise argparse.ArgumentTypeError("Amount must be positive and fit six decimal places")
    if amount != amount.quantize(Decimal("0.000001")):
        raise argparse.ArgumentTypeError("Amount must have at most six decimal places")
    return amount


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("A positive integer is required") from error
    if number < 1:
        raise argparse.ArgumentTypeError("A positive integer is required")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--daily-inference-cap-usd", required=True, type=_money)
    parser.add_argument("--monthly-inference-cap-usd", required=True, type=_money)
    parser.add_argument("--max-concurrent-runs", required=True, type=_positive_int)
    args = parser.parse_args()
    if args.monthly_inference_cap_usd < args.daily_inference_cap_usd:
        parser.error("Monthly inference cap must be at least the daily cap")
    url = os.environ.get("AIP_DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://"):
        parser.error("AIP_DATABASE_URL must point to migrated PostgreSQL")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as db, db.begin():
            tenant = db.scalar(
                select(Tenant).where(Tenant.id == args.tenant_id).with_for_update()
            )
            if tenant is None:
                parser.error("Tenant not found")
            before = {
                "daily_inference_cap_usd": format(tenant.daily_inference_cap_usd, ".6f"),
                "monthly_inference_cap_usd": format(tenant.monthly_inference_cap_usd, ".6f"),
                "max_concurrent_runs": tenant.max_concurrent_runs,
            }
            after = {
                "daily_inference_cap_usd": format(args.daily_inference_cap_usd, ".6f"),
                "monthly_inference_cap_usd": format(args.monthly_inference_cap_usd, ".6f"),
                "max_concurrent_runs": args.max_concurrent_runs,
            }
            if before != after:
                tenant.daily_inference_cap_usd = args.daily_inference_cap_usd
                tenant.monthly_inference_cap_usd = args.monthly_inference_cap_usd
                tenant.max_concurrent_runs = args.max_concurrent_runs
                db.add(
                    AuditEvent(
                        tenant_id=tenant.id,
                        actor="trusted-operator",
                        action="tenant.quotas.update",
                        target_ref=tenant.id,
                        arguments_hash=canonical_hash({"before": before, "after": after}),
                        policy_revision=tenant.policy_revision,
                        outcome="allowed",
                    )
                )
        print(f"Tenant {args.tenant_id} quotas set")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
