from decimal import Decimal

import pytest

from platform_app.token_budget import (
    BudgetError,
    PriceRule,
    TokenPolicy,
    check_cumulative_budget,
    plan_call,
)

PRICE = PriceRule("rev-1", Decimal("4"), Decimal("20"))


def test_input_output_and_margin_fit_verified_window():
    plan = plan_call(32000, 4000, 20000, 500, TokenPolicy(), PRICE)
    assert plan.envelope == 32000
    assert plan.input_capacity == 25500
    assert plan.output_reserve == 4000
    assert plan.should_compact is False
    assert plan.max_liability_usd > 0


def test_unknown_limits_and_overflow_fail_closed():
    with pytest.raises(BudgetError, match="unverified"):
        plan_call(None, 4000, 100, 100, TokenPolicy(), PRICE)
    with pytest.raises(BudgetError, match="cannot fit"):
        plan_call(32000, 4000, 30000, 500, TokenPolicy(), PRICE)


def test_reserved_spend_counts_before_next_call():
    policy = TokenPolicy(spend_limit_usd=Decimal("0.10"))
    plan = plan_call(32000, 4000, 20000, 500, policy, PRICE)
    with pytest.raises(BudgetError, match="run spend limit"):
        check_cumulative_budget(policy, 0, 0, Decimal("0.01"), Decimal("0.01"), plan)
