"""Deterministic preflight and reservation planning for model calls (FR-TOK-01/03)."""

from dataclasses import dataclass
from decimal import ROUND_UP, Decimal


class BudgetError(Exception):
    pass


class RunSpendExhausted(BudgetError):
    pass


@dataclass(frozen=True)
class TokenPolicy:
    application_envelope: int = 64000
    generation_reserve: int = 8000
    minimum_safety_margin: int = 2000
    safety_margin_fraction: Decimal = Decimal("0.05")
    compaction_trigger_fraction: Decimal = Decimal("0.80")
    cumulative_input_limit: int = 300000
    cumulative_output_limit: int = 60000
    spend_limit_usd: Decimal = Decimal("5.00")

    def __post_init__(self):
        if min(self.application_envelope, self.generation_reserve, self.minimum_safety_margin) <= 0:
            raise ValueError("Token limits must be positive")
        if not Decimal(0) < self.safety_margin_fraction < Decimal(1):
            raise ValueError("Safety margin fraction must be in (0, 1)")
        if not Decimal(0) < self.compaction_trigger_fraction < Decimal(1):
            raise ValueError("Compaction trigger must be in (0, 1)")


@dataclass(frozen=True)
class PriceRule:
    revision: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def __post_init__(self):
        if not self.revision or min(self.input_usd_per_million, self.output_usd_per_million) < 0:
            raise ValueError("Verified nonnegative pricing is required")


@dataclass(frozen=True)
class TokenPlan:
    envelope: int
    input_tokens: int
    output_reserve: int
    safety_margin: int
    input_capacity: int
    should_compact: bool
    max_liability_usd: Decimal


def plan_call(
    model_context_limit: int | None,
    model_output_limit: int | None,
    input_tokens: int,
    provider_overhead_tokens: int,
    policy: TokenPolicy,
    price: PriceRule | None,
) -> TokenPlan:
    if model_context_limit is None or model_output_limit is None:
        raise BudgetError("CONTEXT_UNSATISFIABLE: model limits are unverified")
    if price is None:
        raise BudgetError("BUDGET_EXHAUSTED: model pricing is unverified")
    if input_tokens < 0 or provider_overhead_tokens < 0:
        raise BudgetError("Token estimates cannot be negative")
    envelope = min(model_context_limit, policy.application_envelope)
    output = min(model_output_limit, policy.generation_reserve)
    margin = max(
        policy.minimum_safety_margin,
        int(
            (Decimal(envelope) * policy.safety_margin_fraction).to_integral_value(rounding=ROUND_UP)
        ),
    )
    capacity = envelope - output - margin - provider_overhead_tokens
    if capacity <= 0 or input_tokens > capacity:
        raise BudgetError("CONTEXT_UNSATISFIABLE: required input cannot fit")
    liability = (
        Decimal(input_tokens + provider_overhead_tokens) * price.input_usd_per_million
        + Decimal(output) * price.output_usd_per_million
    ) / Decimal(1_000_000)
    return TokenPlan(
        envelope=envelope,
        input_tokens=input_tokens,
        output_reserve=output,
        safety_margin=margin,
        input_capacity=capacity,
        should_compact=Decimal(input_tokens)
        >= (Decimal(capacity) * policy.compaction_trigger_fraction),
        max_liability_usd=liability.quantize(Decimal("0.000001"), rounding=ROUND_UP),
    )


def check_cumulative_budget(
    policy: TokenPolicy,
    used_input: int,
    used_output: int,
    committed_usd: Decimal,
    reserved_usd: Decimal,
    plan: TokenPlan,
) -> None:
    if used_input + plan.input_tokens > policy.cumulative_input_limit:
        raise BudgetError("BUDGET_EXHAUSTED: cumulative input token limit")
    if used_output + plan.output_reserve > policy.cumulative_output_limit:
        raise BudgetError("BUDGET_EXHAUSTED: cumulative output token limit")
    if committed_usd + reserved_usd + plan.max_liability_usd > policy.spend_limit_usd:
        raise RunSpendExhausted("BUDGET_EXHAUSTED: run spend limit")
