"""Reserve model liability before an external call and settle reported usage."""

from __future__ import annotations

from decimal import ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.action_policy import ActionIntent, authorize_run_effect
from platform_app.models import BudgetEntry, ModelEntry, Run, ToolAction
from platform_app.run_ledger import assert_fence, complete_tool_action
from platform_app.service import ServiceError, append_event, canonical_hash
from platform_app.token_budget import (
    BudgetError,
    PriceRule,
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
    )


def _cost(input_tokens: int, output_tokens: int, price: PriceRule) -> Decimal:
    value = (
        Decimal(input_tokens) * price.input_usd_per_million
        + Decimal(output_tokens) * price.output_usd_per_million
    ) / Decimal(1_000_000)
    return value.quantize(Decimal("0.000001"), rounding=ROUND_UP)


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
    assert_fence(run, worker_id, fence)
    if run.cancel_requested:
        raise ServiceError("RUN_CANCELLED", "Cancellation stops model calls", 409)
    if model.id != run.model_entry_id or model.state != "enabled":
        raise ServiceError("MODEL_UNAVAILABLE", "Run model changed or is disabled", 409)
    if not model.validated_at or not (model.capabilities or {}).get("live_qualified"):
        raise ServiceError("MODEL_UNAVAILABLE", "Live provider qualification is required", 409)
    snapshot = run.config_snapshot
    if (
        snapshot.get("model_registry_revision") != model.registry_revision
        or snapshot.get("model_price_revision") != model.price_revision
        or snapshot.get("model_context_limit") != model.context_limit
        or snapshot.get("model_output_limit") != model.output_limit
        or snapshot.get("model_price_per_m_input") != str(model.price_per_m_input)
        or snapshot.get("model_price_per_m_output") != str(model.price_per_m_output)
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
    except BudgetError as error:
        raise ServiceError("BUDGET_EXHAUSTED", str(error), 409) from error
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
    return action, reservation, plan


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
    if usage.get("cache_read_tokens", 0) or usage.get("cache_creation_tokens", 0):
        raise ServiceError(
            "USAGE_PRICING_UNQUALIFIED", "Cached token pricing is not qualified", 409
        )
    if len(output_sha256) != 64 or any(c not in "0123456789abcdef" for c in output_sha256):
        raise ServiceError("RECEIPT_INVALID", "Model output digest is invalid", 400)
    actual = _cost(inputs, outputs, _price(model))
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
            "usage": {"input_tokens": inputs, "output_tokens": outputs},
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
    return actual
