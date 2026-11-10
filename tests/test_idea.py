import json
from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from test_planner import data, make_runner

from travel_agent.deepseek import DeepSeekSettings, ProviderError
from travel_agent.idea import extract_idea
from travel_agent.service_budget import PriceCard, ServiceBudget
from travel_agent.web_api import WebPlanRequest, run_plan


def response(fields=None, usage=None):
    fields = fields or {
        "destination": "南京",
        "budget": None,
        "origin": None,
        "days_text": None,
        "adults_text": None,
        "start_date": None,
    }
    return httpx.Response(
        200,
        json={
            "model": "deepseek-flash",
            "usage": usage
            if usage is not None
            else {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(fields)}}],
        },
    )


def test_first_idea_preserves_unknowns_and_shared_ledger():
    ledger = ServiceBudget()
    planning, assumptions = extract_idea(
        "南京玩玩",
        DeepSeekSettings("test"),
        PriceCard(),
        ledger,
        transport=httpx.MockTransport(lambda request: response()),
    )
    assert planning.city == "南京" and planning.trip.days == 1
    assert planning.trip.adults is None and planning.trip.budget is None
    assert len(assumptions) == 4 and ledger.summary()["estimated_upper"] == 600
    runner, counts = make_runner()
    result = runner.run(planning, ledger=ledger, initial_model_calls=1)
    assert result["status"] == "draft" and result["model_calls"] == 4
    assert counts["model"] == 3 and result["service_cost"]["estimated_upper"] == 7400
    assert result["plan"]["travel_budget"] is None


def test_shared_cost_limit_can_stop_after_extraction_before_planning():
    ledger = ServiceBudget(25)
    planning, _ = extract_idea(
        "南京玩玩",
        DeepSeekSettings("test"),
        PriceCard(),
        ledger,
        transport=httpx.MockTransport(
            lambda request: response(usage={"prompt_tokens": 4000, "completion_tokens": 500})
        ),
    )
    runner, counts = make_runner()
    result = runner.run(planning, ledger=ledger, initial_model_calls=1)
    assert result["issue"] == "service_budget_exhausted" and counts["model"] == 0
    assert result["service_cost"]["cap"] == 250000
    assert result["service_cost"]["estimated_upper"] == 12000


def test_idea_fields_must_be_grounded_in_original_and_usage_known():
    ledger = ServiceBudget()
    with pytest.raises(ProviderError, match="ungrounded_extraction"):
        extract_idea(
            "上海",
            DeepSeekSettings("test"),
            PriceCard(),
            ledger,
            transport=httpx.MockTransport(lambda request: response()),
        )
    assert ledger.summary()["estimated_upper"] == 600
    ledger = ServiceBudget()
    with pytest.raises(ProviderError, match="model_usage_or_price_unknown"):
        extract_idea(
            "南京",
            DeepSeekSettings("test"),
            PriceCard(),
            ledger,
            transport=httpx.MockTransport(lambda request: response(usage={})),
        )
    assert ledger.uncertain and ledger.reservations


def test_explicit_count_and_budget_are_not_replaced_by_defaults():
    fields = {
        "destination": "南京",
        "budget": "每人2000元",
        "origin": "上海",
        "days_text": "两天",
        "adults_text": "2个成人",
        "start_date": "2026-11-12",
    }
    planning, _ = extract_idea(
        "从上海去南京两天，2个成人，每人2000元，2026-11-12",
        DeepSeekSettings("test"),
        PriceCard(),
        ServiceBudget(),
        transport=httpx.MockTransport(lambda request: response(fields)),
    )
    assert planning.trip.days == 2 and planning.trip.adults == 2
    assert planning.trip.start_date == date(2026, 11, 12)
    assert planning.trip.budget == "每人2000元" and planning.trip.budget_scope is None


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"idea": "  "},
        {"idea": "南京", "planning": data().model_dump()},
    ],
)
def test_task_requires_exactly_one_nonblank_input(fields):
    with pytest.raises(ValidationError):
        WebPlanRequest(request_id="00000000-0000-4000-8000-000000000000", **fields)


def test_web_idea_orchestration_preserves_same_limits_and_fills_editable_conditions(
    monkeypatch, tmp_path
):
    runner, counts = make_runner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("travel_agent.web_api.BaselineRunner", lambda *a, **k: runner)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setenv("AMAP_WEB_API_KEY", "test")

    def extracted(text, settings, prices, ledger):
        ledger.reserve("idea_model", 600)
        ledger.settle("idea_model", 600)
        return data(), ["测试假设"]

    monkeypatch.setattr("travel_agent.web_api.extract_idea", extracted)
    output = run_plan(
        WebPlanRequest(request_id="00000000-0000-4000-8000-000000000000", idea="南京")
    )
    assert output["planning"]["city"] == "南京"
    assert output["assumptions"] == ["测试假设"] and output["audit_saved"]
    assert output["result"]["model_calls"] == 4 and counts["model"] == 3


def test_web_idea_failure_reports_cost_instead_of_losing_it(monkeypatch):
    runner, counts = make_runner()
    monkeypatch.setattr("travel_agent.web_api.BaselineRunner", lambda *a, **k: runner)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setenv("AMAP_WEB_API_KEY", "test")

    def broken(text, settings, prices, ledger):
        ledger.reserve("idea_model", 240000)
        ledger.unknown()
        raise ProviderError("upstream_timeout")

    monkeypatch.setattr("travel_agent.web_api.extract_idea", broken)
    output = run_plan(
        WebPlanRequest(request_id="00000000-0000-4000-8000-000000000000", idea="南京")
    )
    assert output["result"]["issue"] == "upstream_timeout" and counts["model"] == 0
    assert output["result"]["service_cost"]["status"] == "uncertain"


def test_followup_inherits_old_conditions_and_changes_only_explicit_fields():
    from travel_agent.planning_models import PlanningInput

    base = PlanningInput(
        city="南京",
        trip={
            "destination": "南京",
            "raw_text": "南京想看展",
            "days": 2,
            "adults": 2,
            "budget": "总预算2000以内",
            "budget_scope": "total",
        },
        constraints={"max_sightseeing_places": 1},
    )
    fields = {
        "destination": None,
        "budget": "每人3000元以内",
        "origin": None,
        "days_text": None,
        "adults_text": None,
        "start_date": None,
    }
    revised, assumptions = extract_idea(
        "预算改为每人3000元以内",
        DeepSeekSettings("test"),
        PriceCard(),
        ServiceBudget(),
        transport=httpx.MockTransport(lambda request: response(fields)),
        base=base,
    )
    assert revised.city == "南京" and revised.trip.days == revised.trip.adults == 2
    assert revised.trip.budget == "每人3000元以内" and revised.trip.budget_scope is None
    assert revised.constraints.max_sightseeing_places == 1
    assert "南京想看展" in revised.trip.raw_text and "预算改为" in revised.trip.raw_text
    assert any("沿用上一版" in text for text in assumptions)
