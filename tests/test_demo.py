"""The demo is explicit and cannot reach real providers, even with real-looking env keys."""

from uuid import uuid4

import httpx
import pytest

from travel_agent.deepseek import ProviderError
from travel_agent.demo import demo_intake, demo_runner
from travel_agent.parsing import intake
from travel_agent.planner import validate_proposal
from travel_agent.planning_models import DayOverride, PlanningInput, ProductProposal
from travel_agent.service_budget import ServiceBudget
from travel_agent.source_models import PlaceHit, SourceBatch
from travel_agent.web_api import WebPlanRequest, run_plan


def test_demo_uses_no_environment_credentials_or_network(monkeypatch):
    monkeypatch.setenv("TRAVEL_DEMO_MODE", "1")
    monkeypatch.setenv("TRAVEL_WEB_LIVE", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-read")
    monkeypatch.setenv("AMAP_WEB_API_KEY", "must-not-read")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("network"))
    output = run_plan(WebPlanRequest(request_id=uuid4(), idea="杭州两天，预算2000以内"))
    assert output["is_demo"] and output["result"]["is_demo"]
    assert output["planning"]["trip"]["budget"] == "2000以内"
    assert output["result"]["status"] == "draft"
    assert output["result"]["actual_model_calls"] == output["result"]["actual_source_calls"] == 0
    assert output["result"]["service_cost"]["estimated_upper"] == 0
    assert "合成演示数据" in output["markdown"]
    changed = run_plan(
        WebPlanRequest(request_id=uuid4(), idea="第二天下午五点结束"),
        base_planning=PlanningInput.model_validate(output["planning"]),
        base_output=output,
    )
    assert changed["result"]["model_calls"] == 0

    def first(value):
        return [a for a in value["result"]["plan"]["activities"] if a["day"] == 1]

    assert first(changed) == first(output)


@pytest.mark.parametrize(
    "text", ["北京两天", "南京附近两天", "南京上海两天", "南京14天", "杭州四天", "上海1天或2天"]
)
def test_demo_rejects_unsupported_scope(text):
    with pytest.raises(ProviderError):
        demo_intake(text)


def test_modes_cannot_mix_or_reuse_each_others_sources(monkeypatch):
    monkeypatch.setenv("TRAVEL_DEMO_MODE", "1")
    monkeypatch.setenv("TRAVEL_WEB_LIVE", "1")
    data = WebPlanRequest(request_id=uuid4(), idea="南京一天")
    with pytest.raises(ProviderError, match="demo_live_mode_conflict"):
        run_plan(data)
    monkeypatch.setenv("TRAVEL_WEB_LIVE", "0")
    with pytest.raises(ProviderError, match="base_execution_mode_mismatch"):
        run_plan(data, base_output={"is_demo": False})


def test_fresh_research_and_validator_honor_day_scoped_excluded_ids():
    runner = demo_runner("single_agent")
    planning, _ = demo_intake("南京两天")
    first = runner.run(planning, ledger=ServiceBudget())
    excluded = next(a for a in first["plan"]["activities"] if a["day"] == 2)
    planning = planning.model_copy(
        update={
            "day_overrides": (
                DayOverride(day=2, excluded_source_ids=(excluded["place_source_id"],)),
            )
        }
    )
    second = runner.run(planning, ledger=ServiceBudget())
    assert all(
        a["place_source_id"] != excluded["place_source_id"]
        for a in second["plan"]["activities"]
        if a["day"] == 2
    )
    # Deliberately bypass research filtering: final validation still rejects the forbidden ID.
    activities = [
        {
            k: v
            for k, v in a.items()
            if k in {"day", "place_source_id", "start_minute", "end_minute", "kind"}
        }
        for a in first["plan"]["activities"]
    ]
    checked = validate_proposal(
        ProductProposal.model_validate({"activities": activities}),
        intake(planning.trip),
        [SourceBatch.model_validate(batch) for batch in first["sources"]],
        {},
        constraints=planning.effective_constraints(),
        daily_activity_limit=6,
    )
    assert any(c["rule"] == "excluded_place" and c["status"] == "fail" for c in checked["checks"])


def test_names_alone_cannot_bypass_id_exclusion():
    from travel_agent.product_planner import allowed_on_day

    planning, _ = demo_intake("南京一天")
    runner = demo_runner("single_agent")
    result = runner.run(planning, ledger=ServiceBudget())
    place = next(
        PlaceHit.model_validate(h)
        for b in result["sources"]
        for h in b["hits"]
        if h.get("evidence_kind") == "provider_place_record"
    )
    planning = planning.model_copy(
        update={"day_overrides": (DayOverride(day=1, excluded_source_ids=(place.source_id,)),)}
    )
    assert not allowed_on_day(place.model_copy(update={"name": "改名之后"}), planning, 1)
