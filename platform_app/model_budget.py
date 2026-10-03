"""Reserve model liability before an external call and settle reported usage."""

from __future__ import annotations

from decimal import ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.action_policy import ActionIntent, authorize_run_effect
from platform_app.config import settings
from platform_app.model_qualification import qualification_for_pinned_run
from platform_app.models import AuditEvent, BudgetEntry, ModelEntry, Run, RunEvent, ToolAction
from platform_app.run_ledger import assert_fence, complete_tool_action
from platform_app.service import ServiceError, append_event, canonical_hash
from platform_app.telemetry import set_safe_attributes, tracer
from platform_app.tenant_quota import (
    QuotaError,
    check_inference_reservation,
    lock_tenant,
    settlement_breaches,
)
from platform_app.token_budget import (
    BudgetError,
    PriceRule,
    RunSpendExhausted,
    TokenPlan,
    TokenPolicy,
    check_cumulative_budget,
    plan_call,
)


def _price(model: ModelEntry) -> PriceRule:
    if (
        not model.price_revision
        or model.price_per_m_input is None
        or model.price_per_m_output is None
    ):
        raise ServiceError("MODEL_UNAVAILABLE", "Verified pricing is required", 409)
    return PriceRule(
        revision=model.price_revision,
        input_usd_per_million=Decimal(model.price_per_m_input),
        output_usd_per_million=Decimal(model.price_per_m_output),
        cache_read_usd_per_million=(
            Decimal(model.price_per_m_cache_read)
            if model.price_per_m_cache_read is not None else None
        ),
        cache_write_usd_per_million=(
            Decimal(model.price_per_m_cache_write)
            if model.price_per_m_cache_write is not None else None
        ),
    )


def _pinned_price(run: Run) -> PriceRule:
    snapshot = run.config_snapshot or {}
    try:
        return PriceRule(
            revision=snapshot["model_price_revision"],
            input_usd_per_million=Decimal(snapshot["model_price_per_m_input"]),
            output_usd_per_million=Decimal(snapshot["model_price_per_m_output"]),
            cache_read_usd_per_million=(
                Decimal(snapshot["model_price_per_m_cache_read"])
                if snapshot.get("model_price_per_m_cache_read") is not None else None
            ),
            cache_write_usd_per_million=(
                Decimal(snapshot["model_price_per_m_cache_write"])
                if snapshot.get("model_price_per_m_cache_write") is not None else None
            ),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise ServiceError(
            "MODEL_REVISION_CHANGED", "Pinned model pricing is invalid", 409
        ) from error


def _cost(
    input_tokens: int, output_tokens: int, price: PriceRule,
    cache_read_tokens: int = 0, cache_write_tokens: int = 0,
) -> Decimal:
    if cache_read_tokens + cache_write_tokens > input_tokens:
        raise ServiceError("USAGE_UNKNOWN", "Cached usage exceeds total input", 409)
    if (cache_read_tokens and price.cache_read_usd_per_million is None) or (
        cache_write_tokens and price.cache_write_usd_per_million is None
    ):
        raise ServiceError(
            "USAGE_PRICING_UNQUALIFIED", "Cached token pricing is not qualified", 409
        )
    value = (
        Decimal(input_tokens - cache_read_tokens - cache_write_tokens)
        * price.input_usd_per_million
        + Decimal(cache_read_tokens) * (price.cache_read_usd_per_million or Decimal(0))
        + Decimal(cache_write_tokens) * (price.cache_write_usd_per_million or Decimal(0))
        + Decimal(output_tokens) * price.output_usd_per_million
    ) / Decimal(1_000_000)
    return value.quantize(Decimal("0.000001"), rounding=ROUND_UP)


@tracer.start_as_current_span("model.reserve")
def reserve_model_call(
    db: Session,
    run: Run,
    worker_id: str,
    fence: int,
    model: ModelEntry,
    step_id: str,
    prompt: str,
    provider_overhead_tokens: int = 4096,
) -> tuple[ToolAction, BudgetEntry, TokenPlan]:
    """Caller commits this transaction before invoking the provider."""
    set_safe_attributes(run_id=run.id, model_entry_id=model.id, model_step=step_id)
    # Match the emergency-disable lock order. Re-read both rows under locks so
    # a worker that loaded them earlier cannot reserve against stale policy.
    locked_model = db.scalar(select(ModelEntry).where(
        ModelEntry.id == model.id
    ).with_for_update().execution_options(populate_existing=True))
    locked_run = db.scalar(select(Run).where(
        Run.id == run.id
    ).with_for_update().execution_options(populate_existing=True))
    if locked_model is None or locked_run is None:
        raise ServiceError("MODEL_UNAVAILABLE", "Run model is unavailable", 409)
    model, run = locked_model, locked_run
    assert_fence(run, worker_id, fence)
    if run.cancel_requested:
        raise ServiceError("RUN_CANCELLED", "Cancellation stops model calls", 409)
    if model.id != run.model_entry_id or model.state not in {"enabled", "deprecated"}:
        raise ServiceError("MODEL_UNAVAILABLE", "Run model changed or is disabled", 409)
    controlled_fixture = bool(
        settings().environment == "development"
        and model.validated_at
        and (model.capabilities or {}).get("controlled_provider_fixture") is True
    )
    if not qualification_for_pinned_run(model) and not controlled_fixture:
        raise ServiceError("MODEL_UNAVAILABLE", "Live provider qualification is required", 409)
    snapshot = run.config_snapshot
    if (
        snapshot.get("model_registry_revision") != model.registry_revision
        or snapshot.get("model_price_revision") != model.price_revision
        or snapshot.get("model_context_limit") != model.context_limit
        or snapshot.get("model_output_limit") != model.output_limit
        or snapshot.get("model_price_per_m_input") != str(model.price_per_m_input)
        or snapshot.get("model_price_per_m_output") != str(model.price_per_m_output)
        or snapshot.get("model_price_per_m_cache_read") != (
            str(model.price_per_m_cache_read)
            if model.price_per_m_cache_read is not None else None
        )
        or snapshot.get("model_price_per_m_cache_write") != (
            str(model.price_per_m_cache_write)
            if model.price_per_m_cache_write is not None else None
        )
    ):
        raise ServiceError("MODEL_REVISION_CHANGED", "Run model snapshot no longer matches", 409)
    if (
        len(step_id) > 40
        or not step_id
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in step_id)
    ):
        raise ServiceError("INVALID_STEP", "Model step identifier is invalid", 400)

    price = _price(model)
    policy = TokenPolicy(spend_limit_usd=Decimal(str(run.config_snapshot["spend_limit_usd"])))
    # One byte per estimated token is conservative for text-only calls. Images
    # and provider tools are excluded from this path until separately metered.
    estimated_input = len(prompt.encode("utf-8"))
    try:
        plan = plan_call(
            model.context_limit,
            model.output_limit,
            estimated_input,
            provider_overhead_tokens,
            policy,
            price,
        )
    except BudgetError as error:
        raise ServiceError("BUDGET_EXHAUSTED", str(error), 409) from error

    actions = db.scalars(
        select(ToolAction).where(
            ToolAction.run_id == run.id,
            ToolAction.logical_action == "model.generate",
        )
    ).all()
    if any(action.status == "INTENDED" for action in actions):
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Unsettled model call requires review", 409)
    arguments = {
        "model_entry_id": model.id,
        "model_revision": model.registry_revision,
        "price_revision": price.revision,
        "prompt_sha256": canonical_hash(prompt),
        "estimated_input_tokens": estimated_input,
        "output_reserve": plan.output_reserve,
    }
    existing = next((action for action in actions if action.step_id == step_id), None)
    if existing is not None:
        action = authorize_run_effect(
            db,
            run,
            worker_id,
            fence,
            ActionIntent(
                step_id,
                "model.generate",
                "provider_request",
                model.id,
                model.registry_revision,
                arguments,
            ),
        )
        budget = db.scalar(
            select(BudgetEntry).where(
                BudgetEntry.run_id == run.id,
                BudgetEntry.category == f"call:{step_id}",
            )
        )
        if budget is None:
            raise ServiceError("LEDGER_INCOMPLETE", "Model reservation is missing", 409)
        return action, budget, plan
    if len(actions) >= int(run.config_snapshot["max_model_calls"]):
        raise ServiceError("BUDGET_EXHAUSTED", "Model call limit reached", 409)

    entries = db.scalars(
        select(BudgetEntry).where(
            BudgetEntry.run_id == run.id,
            BudgetEntry.category.like("call:%"),
        )
    ).all()
    committed = sum((Decimal(entry.actual_usd) for entry in entries), Decimal(0))
    reserved = sum(
        (Decimal(entry.reserved_usd) for entry in entries if entry.status == "reserved"), Decimal(0)
    )
    used_input = sum(
        int((action.receipt or {}).get("usage", {}).get("input_tokens", 0)) for action in actions
    )
    used_output = sum(
        int((action.receipt or {}).get("usage", {}).get("output_tokens", 0)) for action in actions
    )
    try:
        check_cumulative_budget(policy, used_input, used_output, committed, reserved, plan)
    except RunSpendExhausted as error:
        raise ServiceError("RUN_SPEND_EXHAUSTED", str(error), 409) from error
    except BudgetError as error:
        raise ServiceError("BUDGET_EXHAUSTED", str(error), 409) from error
    try:
        tenant = lock_tenant(db, run.tenant_id)
        warnings = check_inference_reservation(db, tenant, plan.max_liability_usd)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 409) from error
    action = authorize_run_effect(
        db,
        run,
        worker_id,
        fence,
        ActionIntent(
            step_id,
            "model.generate",
            "provider_request",
            model.id,
            model.registry_revision,
            arguments,
        ),
    )
    reservation = BudgetEntry(
        tenant_id=run.tenant_id,
        run_id=run.id,
        category=f"call:{step_id}",
        reserved_usd=plan.max_liability_usd,
        actual_usd=Decimal(0),
        status="reserved",
    )
    db.add(reservation)
    append_event(
        db,
        run,
        "model.started",
        {
            "step_id": step_id,
            "model_entry_id": model.id,
            "context_sha256": arguments["prompt_sha256"],
            "estimated_input_tokens": estimated_input,
            "reserved_usd": str(plan.max_liability_usd),
        },
    )
    append_event(
        db,
        run,
        "budget.updated",
        {
            "step_id": step_id,
            "reserved_usd": str(plan.max_liability_usd),
        },
    )
    for scope in warnings:
        append_event(db, run, "budget.warning", {"scope": scope, "threshold_percent": 80})
    return action, reservation, plan


@tracer.start_as_current_span("model.settle")
def settle_model_call(
    db: Session,
    run: Run,
    worker_id: str,
    fence: int,
    action: ToolAction,
    reservation: BudgetEntry,
    model: ModelEntry,
    usage: dict[str, int],
    output_sha256: str,
    artifact_ref: str,
) -> Decimal:
    """Persist provider usage and a receipt; never infer success from model prose."""
    set_safe_attributes(run_id=run.id, model_entry_id=model.id)
    assert_fence(run, worker_id, fence)
    if action.run_id != run.id or reservation.run_id != run.id:
        raise ServiceError("LEDGER_SCOPE", "Model ledger scope mismatch", 409)
    if action.status != "INTENDED" or reservation.status != "reserved":
        raise ServiceError("LEDGER_STATE", "Model call is not awaiting settlement", 409)
    inputs = usage.get("input_tokens")
    outputs = usage.get("output_tokens")
    if (
        not isinstance(inputs, int)
        or isinstance(inputs, bool)
        or inputs < 0
        or not isinstance(outputs, int)
        or isinstance(outputs, bool)
        or outputs < 0
    ):
        raise ServiceError("USAGE_UNKNOWN", "Provider usage is missing or invalid", 409)
    usage_receipt = {"input_tokens": inputs, "output_tokens": outputs}
    for name in ("reasoning_tokens", "cache_read_tokens", "cache_creation_tokens"):
        if name in usage:
            value = usage[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ServiceError("USAGE_UNKNOWN", "Provider usage is invalid", 409)
            usage_receipt[name] = value
    if len(output_sha256) != 64 or any(c not in "0123456789abcdef" for c in output_sha256):
        raise ServiceError("RECEIPT_INVALID", "Model output digest is invalid", 400)
    actual = _cost(
        inputs, outputs, _pinned_price(run),
        usage_receipt.get("cache_read_tokens", 0),
        usage_receipt.get("cache_creation_tokens", 0),
    )
    try:
        tenant = lock_tenant(db, run.tenant_id, require_active=False)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 403) from error
    breaches = settlement_breaches(db, tenant, reservation, actual)
    reservation.actual_usd = actual
    reservation.status = "settled" if actual <= Decimal(reservation.reserved_usd) else "overrun"
    complete_tool_action(
        db,
        run,
        worker_id,
        fence,
        action,
        {
            "status": "COMPLETED",
            "usage": usage_receipt,
            "output_sha256": output_sha256,
            "artifact_ref": artifact_ref,
            "actual_usd": str(actual),
        },
    )
    append_event(
        db,
        run,
        "model.completed",
        {
            "step_id": action.step_id,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "actual_usd": str(actual),
            "overrun": reservation.status == "overrun",
        },
    )
    append_event(
        db,
        run,
        "budget.updated",
        {
            "step_id": action.step_id,
            "actual_usd": str(actual),
        },
    )
    for scope in breaches:
        append_event(
            db, run, "budget.breached",
            {"scope": scope, "step_id": action.step_id, "actual_usd": str(actual)},
        )
    return actual


@tracer.start_as_current_span("model.reject")
def reject_model_call(
    db: Session,
    run: Run,
    worker_id: str,
    fence: int,
    action: ToolAction,
    reservation: BudgetEntry,
    error_code: str,
    status_code: int,
) -> None:
    """Release liability only for a definitive HTTP rejection with no model result."""
    assert_fence(run, worker_id, fence)
    if status_code not in {400, 401, 403, 404, 429}:
        raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Provider outcome requires review", 409)
    if (
        action.tenant_id != run.tenant_id or action.run_id != run.id
        or reservation.tenant_id != run.tenant_id or reservation.run_id != run.id
        or action.status != "INTENDED" or reservation.status != "reserved"
        or reservation.category != f"call:{action.step_id}"
    ):
        raise ServiceError("LEDGER_STATE", "Model rejection ledger does not match", 409)
    try:
        lock_tenant(db, run.tenant_id, require_active=False)
    except QuotaError as error:
        raise ServiceError(error.code, str(error), 403) from error
    reservation.status = "released"
    reservation.actual_usd = Decimal(0)
    complete_tool_action(
        db, run, worker_id, fence, action,
        {"status": "REJECTED", "error_code": error_code, "http_status": status_code},
    )
    append_event(db, run, "model.rejected", {
        "step_id": action.step_id, "error_code": error_code, "http_status": status_code,
    })
    append_event(db, run, "budget.updated", {
        "step_id": action.step_id, "reserved_usd": "0", "actual_usd": "0",
    })


def record_uncertain_model_call(
    db: Session, run: Run, worker_id: str, fence: int,
    action: ToolAction, reservation: BudgetEntry,
    error_code: str, status_code: int | None,
) -> None:
    """Record a failed call without releasing liability or authorizing replay."""
    assert_fence(run, worker_id, fence)
    if (
        action.tenant_id != run.tenant_id or action.run_id != run.id
        or reservation.tenant_id != run.tenant_id or reservation.run_id != run.id
        or action.status != "INTENDED" or reservation.status != "reserved"
        or reservation.category != f"call:{action.step_id}"
    ):
        raise ServiceError("LEDGER_STATE", "Model uncertainty ledger does not match", 409)
    prior = db.scalar(select(RunEvent.id).where(
        RunEvent.tenant_id == run.tenant_id,
        RunEvent.run_id == run.id,
        RunEvent.event_type == "model.uncertain",
        RunEvent.payload["step_id"].as_string() == action.step_id,
    ).limit(1))
    if prior is None:
        append_event(db, run, "model.uncertain", {
            "step_id": action.step_id,
            "error_code": error_code[:80],
            "http_status": status_code if isinstance(status_code, int) else None,
            "liability_status": "reserved",
        })


def record_unreviewed_provider_tool(
    db: Session, run: Run, worker_id: str, fence: int, step_id: str,
) -> None:
    """Record a denied provider tool without retaining its untrusted arguments."""
    assert_fence(run, worker_id, fence)
    prior = db.scalar(select(RunEvent.id).where(
        RunEvent.tenant_id == run.tenant_id,
        RunEvent.run_id == run.id,
        RunEvent.event_type == "tool.denied",
        RunEvent.payload["step_id"].as_string() == step_id,
    ).limit(1))
    if prior is not None:
        return
    append_event(db, run, "tool.denied", {
        "step_id": step_id, "reason": "unreviewed_provider_tool",
    })
    db.add(AuditEvent(
        tenant_id=run.tenant_id, actor=worker_id, action="tool.authorize",
        target_ref=f"{run.id}:{step_id}",
        arguments_hash=canonical_hash({
            "run_id": run.id, "step_id": step_id,
            "reason": "unreviewed_provider_tool",
        }),
        policy_revision=(run.config_snapshot or {}).get("policy_version", "unknown"),
        outcome="denied:unreviewed_provider_tool",
    ))
