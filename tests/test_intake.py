import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from travel_agent.models import BudgetSpec, IntakeInput
from travel_agent.parsing import intake, parse_budget, parse_destination

CASES = json.loads((Path(__file__).parent / "fixtures/intake_cases.json").read_text("utf-8"))


@pytest.mark.regression
@pytest.mark.parametrize("case", CASES)
def test_fixed_intake_semantics(case):
    result = intake(IntakeInput(destination=case["destination"], budget=case["budget"]))
    assert result.destination.mode == case["mode"]
    assert result.budget.kind == case["kind"]
    assert result.budget.maximum_minor == case["cap"]
    assert result.budget.raw_text == case["budget"]
    assert result.input.origin is None
    assert result.status == "draft"
    assert "origin" in result.missing_fields


@pytest.mark.parametrize(
    "raw",
    [
        "-2000",
        "0",
        "3000~2000",
        "500美元",
        "$2000",
        "2000欧元",
        "2000或3000",
        "2000以上",
        "",
        "0.001元",
        "1e5",
        "1,000",
    ],
)
def test_ambiguous_or_invalid_budget_rejected(raw):
    with pytest.raises(ValueError):
        parse_budget(raw)


def test_cents_and_budget_confirmation():
    assert parse_budget("0.29元").target_minor == 29
    assert parse_budget("2000", cap_confirmed=True).maximum_minor == 200000
    assert parse_budget("2000~3000").cap_confirmed is False
    with pytest.raises(ValueError):
        parse_budget("每人2000", scope="total")


@pytest.mark.parametrize("amount", [1, 29, 99, 100, 10001, 299999, 9876543])
def test_money_round_trip_without_float(amount):
    text = f"{amount // 100}.{amount % 100:02d}元以内"
    assert parse_budget(text).maximum_minor == amount


def test_nearby_does_not_invent_origin_or_entity():
    result = intake(IntakeInput(destination=" 南京附近 ", radius_km=80))
    assert result.destination.name == "南京"
    assert result.destination.radius_km == 80
    assert result.destination.entity_status == "unresolved"
    assert "nearby_boundary" not in result.missing_fields
    assert "destination_entity" in result.missing_fields
    assert result.assumptions == ()


@pytest.mark.parametrize("raw", ["附近", "周边", "周边地区", ""])
def test_missing_nearby_anchor_rejected(raw):
    with pytest.raises(ValueError):
        parse_destination(raw)


def test_ranges_require_nearby_destination():
    with pytest.raises(ValueError):
        parse_destination("上海", radius_km=50)
    with pytest.raises(ValueError):
        intake(IntakeInput(radius_km=50))


def test_complete_inputs_still_need_map_resolution():
    result = intake(
        IntakeInput(
            destination="南京",
            origin="上海",
            budget="总预算3000以内",
            adults=2,
            children=1,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 1, 3),
            transport_mode="rail",
            included_categories=("food", "lodging"),
        )
    )
    assert result.days == 3
    assert result.missing_fields == ("destination_entity",)


@pytest.mark.parametrize(
    "payload",
    [
        {"adults": True},
        {"adults": -1},
        {"adults": 1.5},
        {"children": -1},
        {"days": 0},
        {"start_date": "2027-01-03", "end_date": "2027-01-01"},
        {"end_date": "2027-01-01"},
        {"start_date": "2027-01-01", "end_date": "2027-01-03", "days": 2},
        {"destination": " "},
        {"included_categories": []},
        {"included_categories": ["food", "food"]},
        {"buy_now": True},
    ],
)
def test_invalid_intake_contract(payload):
    with pytest.raises(ValidationError):
        IntakeInput.model_validate(payload)


def test_invalid_structured_budget_cannot_bypass_parser():
    for payload in (
        {"kind": "target", "target_minor": 200, "cap_confirmed": True},
        {"kind": "target", "maximum_minor": 200},
        {"kind": "range", "minimum_minor": 300, "maximum_minor": 200},
        {"kind": "ceiling", "maximum_minor": 200, "strict_minimum": True},
        {"kind": "ceiling", "maximum_minor": "200"},
    ):
        with pytest.raises(ValidationError):
            BudgetSpec(raw_text="test", **payload)
