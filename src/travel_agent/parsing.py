"""Conservative parsers for dedicated destination and budget fields, not free-form chat."""

import re
import unicodedata
from decimal import Decimal

from .models import BudgetSpec, DestinationSpec, IntakeInput, TripRequest

NUMBER = r"\d+(?:\.\d+)?"
AMOUNT = rf"({NUMBER})(万|千)?(?:元|块)?"


def parse_budget(raw: str, scope=None, cap_confirmed=False) -> BudgetSpec:
    text = unicodedata.normalize("NFKC", raw)
    text = re.sub(r"\s+", "", text)
    detected = None
    for prefix, value in (
        ("每人每天", "per_person_per_day"),
        ("人均每天", "per_person_per_day"),
        ("每人", "per_person"),
        ("人均", "per_person"),
        ("每天", "per_day"),
        ("总预算", "total"),
        ("总计", "total"),
    ):
        if text.startswith(prefix):
            detected, text = value, text[len(prefix) :]
            break
    if detected and scope and detected != scope:
        raise ValueError("预算文本与预算口径冲突")
    scope = detected or scope
    text = re.sub(r"^预算[:：]?", "", text)
    text = text.replace("人民币", "").replace("CNY", "").replace("¥", "")

    def cents(number, unit):
        amount = Decimal(number) * {None: 1, "万": 10000, "千": 1000}[unit] * 100
        if amount <= 0 or amount != amount.to_integral_value() or amount > 10**12:
            raise ValueError("金额必须为正数，精确到分，且不超过100亿元")
        return int(amount)

    interval = re.fullmatch(rf"{AMOUNT}(?:~|～|到|至|-){AMOUNT}", text)
    if interval:
        a, au, b, bu = interval.groups()
        # A shared trailing unit applies to both bounds, e.g. 2~3千.
        low, high = cents(a, au or bu), cents(b, bu)
        return BudgetSpec(
            raw_text=raw,
            kind="range",
            minimum_minor=low,
            maximum_minor=high,
            scope=scope,
            cap_confirmed=cap_confirmed,
        )
    ceiling = re.fullmatch(rf"(?:不超过|最多|上限)?{AMOUNT}(以内|以下)?", text)
    if not ceiling:
        raise ValueError("预算格式无法确定；请填写如5000以内、2000元或2000~3000")
    number, unit, suffix = ceiling.groups()
    is_cap = bool(suffix or text.startswith(("不超过", "最多", "上限")))
    amount = cents(number, unit)
    if cap_confirmed:
        is_cap = True
    return BudgetSpec(
        raw_text=raw,
        kind="ceiling" if is_cap else "target",
        maximum_minor=amount if is_cap else None,
        target_minor=None if is_cap else amount,
        scope=scope,
        cap_confirmed=is_cap,
    )


def parse_destination(raw: str, radius_km=None, max_one_way_minutes=None) -> DestinationSpec:
    text = unicodedata.normalize("NFKC", raw).strip()
    match = re.fullmatch(r"(.+?)(?:附近|周边)(?:地区)?", text)
    name = (match.group(1) if match else text).strip()
    if not name or text in ("附近", "周边", "附近地区", "周边地区"):
        raise ValueError("周边请求需要明确搜索中心")
    if not match and (radius_km is not None or max_one_way_minutes is not None):
        raise ValueError("具体目的地不能附带未说明的周边范围")
    return DestinationSpec(
        raw_text=raw,
        mode="nearby" if match else "exact",
        name=name,
        radius_km=radius_km,
        max_one_way_minutes=max_one_way_minutes,
    )


def intake(data: IntakeInput) -> TripRequest:
    destination = (
        parse_destination(data.destination, data.radius_km, data.max_one_way_minutes)
        if data.destination
        else None
    )
    if destination is None and (data.radius_km or data.max_one_way_minutes):
        raise ValueError("周边范围需要目的地中心")
    budget = (
        parse_budget(data.budget, data.budget_scope, data.cap_confirmed) if data.budget else None
    )
    days = data.days
    if data.start_date and data.end_date:
        days = (data.end_date - data.start_date).days + 1
    missing = []
    for field, value in (
        ("destination", destination),
        ("budget", budget),
        ("origin", data.origin),
        ("adults", data.adults),
        ("start_date", data.start_date),
        ("days", days),
        ("transport_mode", data.transport_mode),
        ("included_categories", data.included_categories),
    ):
        if value is None:
            missing.append(field)
    if budget:
        if budget.scope is None:
            missing.append("budget_scope")
        if not budget.cap_confirmed:
            missing.append("budget_cap_confirmation")
    if destination:
        missing.append("destination_entity")
        if destination.mode == "nearby" and not (
            destination.radius_km or destination.max_one_way_minutes
        ):
            missing.append("nearby_boundary")
    return TripRequest(
        input=data,
        destination=destination,
        budget=budget,
        days=days,
        missing_fields=tuple(missing),
        assumptions=(),
        status="draft" if missing else "input_complete",
    )
