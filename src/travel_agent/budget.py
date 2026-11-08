"""Deterministic interval arithmetic; unknown costs never silently become zero."""

from .models import LedgerInput, ValidationReport


def check_budget(data: LedgerInput) -> ValidationReport:
    low = high = data.reserve_minor
    unknowns = []
    covered = {item.category for item in data.items}
    unknowns.extend(f"missing_category:{c}" for c in data.required_categories if c not in covered)
    for item in data.items:
        factors = {
            "total": (1,),
            "person": (data.travelers,),
            "day": (data.days,),
            "person_day": (data.travelers, data.days),
            "room_night": (data.rooms, data.nights),
        }[item.unit]
        if item.low_minor is None or any(factor is None for factor in factors):
            unknowns.append(f"unknown_cost_or_quantity:{item.item_id}")
            continue
        multiplier = item.quantity
        for factor in factors:
            multiplier *= factor
        low += item.low_minor * multiplier
        high += item.high_minor * multiplier
        if not item.taxes_included:
            unknowns.append(f"unknown_taxes:{item.item_id}")

    budget = data.budget
    cap = minimum = None
    factors = {
        "total": (1,),
        "per_person": (data.travelers,),
        "per_day": (data.days,),
        "per_person_per_day": (data.travelers, data.days),
        None: (None,),
    }[budget.scope]
    if budget.maximum_minor and all(factor is not None for factor in factors):
        multiplier = 1
        for factor in factors:
            multiplier *= factor
        cap = budget.maximum_minor * multiplier
        minimum = (budget.minimum_minor or 0) * multiplier
    else:
        unknowns.append("missing_budget_cap_or_scope_quantity")
    if not budget.cap_confirmed:
        unknowns.append("budget_cap_unconfirmed")

    if cap is not None and low > cap:
        status = "over_cap"
    elif unknowns:
        status = "uncertain"
    elif cap is not None and high > cap:
        status = "uncertain"
        unknowns.append("cost_interval_crosses_cap")
    elif budget.strict_minimum and high < minimum:
        status = "below_strict_minimum"
    elif budget.strict_minimum and low < minimum:
        status = "uncertain"
        unknowns.append("cost_interval_crosses_minimum")
    else:
        status = "within_estimated_cap"
    # Unknown amount/tax/category makes the full cost upper bound unavailable.
    incomplete_cost = any(u.startswith(("missing_category:", "unknown_")) for u in unknowns)
    return ValidationReport(
        status=status,
        low_minor=low,
        high_minor=None if incomplete_cost else high,
        cap_minor=cap,
        unknowns=tuple(unknowns),
    )
