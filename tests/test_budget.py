import pytest
from pydantic import ValidationError

from travel_agent.budget import check_budget
from travel_agent.models import CostItem, LedgerInput
from travel_agent.parsing import parse_budget


def ledger(**kwargs):
    return LedgerInput(
        budget=kwargs.pop("budget", parse_budget("总预算1000以内")),
        required_categories=kwargs.pop("required_categories", ("food",)),
        **kwargs,
    )


def cost(low=10000, high=10000, **kwargs):
    return CostItem(
        item_id=kwargs.pop("item_id", "food"),
        category=kwargs.pop("category", "food"),
        low_minor=low,
        high_minor=high,
        **kwargs,
    )


def test_group_rooms_nights_tax_and_reserve_counted_once():
    result = check_budget(
        ledger(
            items=(
                cost(unit="person_day"),
                cost(20000, 20000, item_id="hotel", category="lodging", unit="room_night"),
                cost(1000, 1000, item_id="tax", category="fees"),
            ),
            travelers=3,
            days=2,
            rooms=2,
            nights=1,
            reserve_minor=5000,
        )
    )
    assert result.low_minor == result.high_minor == 106000
    assert result.status == "over_cap"


@pytest.mark.parametrize(
    "item,kwargs,reason",
    [
        (cost(None, None), {}, "unknown_cost_or_quantity:food"),
        (cost(unit="person"), {}, "unknown_cost_or_quantity:food"),
        (cost(taxes_included=False), {}, "unknown_taxes:food"),
        (cost(), {"required_categories": ("food", "transport")}, "missing_category:transport"),
    ],
)
def test_unknowns_never_pass(item, kwargs, reason):
    result = check_budget(ledger(items=(item,), **kwargs))
    assert result.status == "uncertain"
    assert result.high_minor is None
    assert reason in result.unknowns


@pytest.mark.parametrize(
    "low,high,status",
    [
        (100000, 100000, "within_estimated_cap"),
        (100001, 100001, "over_cap"),
        (99999, 100001, "uncertain"),
        (50000, 60000, "within_estimated_cap"),
    ],
)
def test_budget_boundary_intervals(low, high, status):
    assert check_budget(ledger(items=(cost(low, high),))).status == status


def test_known_overrun_detected_even_with_unknown_costs():
    result = check_budget(ledger(items=(cost(110000, 110000), cost(None, None, item_id="unknown"))))
    assert result.status == "over_cap"
    assert result.high_minor is None


def test_person_daily_cap_and_multipliers():
    budget = parse_budget("每人每天100以内")
    result = check_budget(
        ledger(budget=budget, items=(cost(5000, 5000, unit="person_day"),), travelers=2, days=3)
    )
    assert result.cap_minor == 60000
    assert result.low_minor == 30000
    assert result.status == "within_estimated_cap"


def test_range_lower_bound_is_preference_unless_strict():
    budget = parse_budget("总预算2000~3000", cap_confirmed=True)
    assert check_budget(ledger(budget=budget, items=(cost(),))).status == "within_estimated_cap"
    strict = type(budget).model_validate({**budget.model_dump(), "strict_minimum": True})
    assert check_budget(ledger(budget=strict, items=(cost(),))).status == "below_strict_minimum"
    assert check_budget(ledger(budget=strict, items=(cost(190000, 210000),))).status == "uncertain"


@pytest.mark.parametrize(
    "budget",
    [
        parse_budget("2000元"),
        parse_budget("1000以内"),
        parse_budget("总预算2000~3000"),
        parse_budget("每人1000以内"),
    ],
)
def test_unconfirmed_budget_or_missing_scope_is_uncertain(budget):
    assert check_budget(ledger(budget=budget, items=(cost(),))).status == "uncertain"


def test_zero_nights_and_quantities():
    result = check_budget(ledger(items=(cost(unit="room_night"),), rooms=2, nights=0))
    assert result.low_minor == 0
    result = check_budget(ledger(items=(cost(unit="day", quantity=2),), days=3))
    assert result.low_minor == 60000


def test_invalid_ledger_and_costs():
    for call in (
        lambda: cost(100, 50),
        lambda: cost(100, None),
        lambda: cost(-1, -1),
        lambda: cost(quantity=0),
        lambda: ledger(items=(cost(), cost())),
        lambda: ledger(items=(), required_categories=()),
        lambda: ledger(items=(), required_categories=("food", "food")),
        lambda: ledger(items=(cost(),), reserve_minor=-1),
    ):
        with pytest.raises(ValidationError):
            call()
