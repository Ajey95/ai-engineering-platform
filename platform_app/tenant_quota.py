"""Serialized tenant quota checks for run admission and inference reservations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from platform_app.models import BudgetEntry, Run, Tenant


class QuotaError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def lock_tenant(db: Session, tenant_id: str, *, require_active: bool = True) -> Tenant:
    # PostgreSQL serializes every admission and reservation for this tenant.
    tenant = db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tenant is None or (require_active and tenant.status != "active"):
        raise QuotaError("TENANT_DISABLED", "Tenant is not active")
    return tenant


def _periods(now: datetime) -> tuple[tuple[str, datetime, datetime], ...]:
    day = datetime(now.year, now.month, now.day, tzinfo=UTC)
    month = datetime(now.year, now.month, 1, tzinfo=UTC)
    next_month = datetime(
        now.year + (now.month == 12), (now.month % 12) + 1, 1, tzinfo=UTC
    )
    return (("daily", day, day + timedelta(days=1)), ("monthly", month, next_month))


def _liability(entry: BudgetEntry) -> Decimal:
    return Decimal(entry.reserved_usd) if entry.status == "reserved" else Decimal(entry.actual_usd)


def inference_usage(db: Session, tenant_id: str, since: datetime, until: datetime) -> Decimal:
    entries = db.scalars(
        select(BudgetEntry).where(
            BudgetEntry.tenant_id == tenant_id,
            BudgetEntry.category.like("call:%"),
            BudgetEntry.created_at >= since,
            BudgetEntry.created_at < until,
        )
    ).all()
    return sum((_liability(entry) for entry in entries), Decimal(0))


def check_admission_quota(db: Session, tenant: Tenant, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    active = db.scalar(
        select(func.count()).select_from(Run).where(
            Run.tenant_id == tenant.id,
            Run.state.not_in(("COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED")),
        )
    )
    if active is None or active >= tenant.max_concurrent_runs:
        raise QuotaError("RUN_CONCURRENCY_EXHAUSTED", "Tenant concurrent run limit reached")
    for label, since, until in _periods(now):
        cap = Decimal(getattr(tenant, f"{label}_inference_cap_usd"))
        if inference_usage(db, tenant.id, since, until) >= cap:
            raise QuotaError("TENANT_BUDGET_EXHAUSTED", f"Tenant {label} inference cap reached")


def check_inference_reservation(
    db: Session, tenant: Tenant, liability: Decimal, now: datetime | None = None
) -> list[str]:
    now = now or datetime.now(UTC)
    if liability <= 0:
        raise QuotaError("TENANT_BUDGET_EXHAUSTED", "Inference liability must be positive")
    warnings = []
    for label, since, until in _periods(now):
        cap = Decimal(getattr(tenant, f"{label}_inference_cap_usd"))
        before = inference_usage(db, tenant.id, since, until)
        after = before + liability
        if after > cap:
            raise QuotaError("TENANT_BUDGET_EXHAUSTED", f"Tenant {label} inference cap reached")
        threshold = cap * Decimal("0.8")
        if before < threshold <= after:
            warnings.append(label)
    return warnings


def settlement_breaches(
    db: Session, tenant: Tenant, reservation: BudgetEntry, actual: Decimal
) -> list[str]:
    booked_at = reservation.created_at or datetime.now(UTC)
    if booked_at.tzinfo is None:
        booked_at = booked_at.replace(tzinfo=UTC)
    old_liability = _liability(reservation)
    breached = []
    for label, since, until in _periods(booked_at):
        cap = Decimal(getattr(tenant, f"{label}_inference_cap_usd"))
        before = inference_usage(db, tenant.id, since, until)
        if before <= cap < before - old_liability + actual:
            breached.append(label)
    return breached
