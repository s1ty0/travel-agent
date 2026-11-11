import json
import time
from datetime import date
from threading import Barrier

import httpx
import pytest

from travel_agent.deepseek import DeepSeekSettings
from travel_agent.planning_models import DayWindow, PlanningInput
from travel_agent.product_planner import ProductPlanner
from travel_agent.scheduler import Stop, arrange
from travel_agent.service_budget import PriceCard, ServiceBudget
from travel_agent.source_models import WalkingPath
from travel_agent.sources import fingerprint

TODAY = date(2026, 11, 11)


def make_product(
    *,
    mode="single_agent",
    delay=0,
    bad_model=False,
    route_minutes=7,
    barrier=None,
    source_error=False,
):
    counts = {"model": 0, "source": 0}

    def model(request):
        counts["model"] += 1
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        if barrier:
            barrier.wait(3)
        time.sleep(delay)
        experience = {
            "visits": [
                {"day": i // 2 + 1, "place_source_id": hit["source_id"], "duration_minutes": 90}
                for i, hit in enumerate(data["places"][: 2 * data["days"]])
            ]
        }
        schema = payload["messages"][0]["content"]
        if '"CombinedResearch"' in schema:
            body = {"experience": experience, "food": {"keyword": "餐厅"}}
        elif '"ExperienceResearch"' in schema:
            body = experience
        else:
            body = {"keyword": "餐厅"}
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "invalid" if bad_model else json.dumps(body)},
                    }
                ],
            },
        )

    def source(request):
        counts["source"] += 1
        time.sleep(delay)
        if source_error:
            return httpx.Response(429, text="private-value")
        if request.url.path.endswith("/tools/search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/guide",
                            "title": "合成资料",
                            "snippet": "信息待核实",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/place/text"):
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "count": "6",
                    "pois": [
                        {
                            "id": f"P{i}",
                            "name": f"测试景点{i}",
                            "address": "合成地址",
                            "location": f"{118.8 + i * 0.001:.6f},32.000000",
                        }
                        for i in range(6)
                    ],
                },
            )
        if request.url.path.endswith("/place/around"):
            anchor = request.url.params["location"]
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "count": "2",
                    "pois": [
                        {"id": f"F{anchor}-{i}", "name": f"测试餐厅{i}", "location": anchor}
                        for i in range(2)
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {"paths": [{"distance": "500", "duration": str(route_minutes * 60)}]},
            },
        )

    return ProductPlanner(
        DeepSeekSettings("synthetic-key"),
        "synthetic-amap",
        PriceCard(amap_free_quota_only=True),
        mode=mode,
        today=TODAY,
        model_transport=httpx.MockTransport(model),
        source_transport=httpx.MockTransport(source),
    ), counts


def planning(days=2, **constraints):
    return PlanningInput(
        city="南京",
        trip={"destination": "南京", "days": days, "budget": "总预算3000以内"},
        day_window=DayWindow(),
        constraints=constraints,
    )


@pytest.mark.parametrize("days", [1, 2, 3])
def test_program_schedules_all_requested_days_with_real_meal_ids_and_routes(days):
    runner, counts = make_product()
    result = runner.run(planning(days), ledger=ServiceBudget())
    assert result["status"] == "draft", result["issue"]
    plan = result["plan"]
    assert {a["day"] for a in plan["activities"]} == set(range(1, days + 1))
    assert sum(a["kind"] == "meal" for a in plan["activities"]) == 2 * days
    assert not any(c["status"] == "fail" for c in plan["checks"])
    assert not plan["facts_verified"] and plan["travel_budget"]["status"] == "uncertain"
    assert counts["model"] == 1 and counts["source"] <= 20
    assert result["service_cost"]["reserved_unsettled"] == 0
    assert all(frame["blocks"][-1]["end_minute"] == 1200 for frame in plan["day_schedule"])


def test_parallel_specialist_intervals_overlap_and_share_one_ledger():
    runner, _ = make_product(mode="multi_agent", delay=0.02, barrier=Barrier(2))
    result = runner.run(planning(1), ledger=ServiceBudget())
    assert result["status"] == "draft"
    spans = [e for e in result["events"] if e["operation"] in {"experience_agent", "food_agent"}]
    assert len(spans) == 2 and all(e["status"] == "ok" for e in spans)
    assert max(e["elapsed_ms"] for e in spans) < min(e["end_ms"] for e in spans)
    assert result["model_calls"] == 2 and result["service_cost"]["cap"] == 5_000_000


def test_known_overlong_walks_stay_failed_and_optional_sight_can_be_removed():
    runner, _ = make_product(route_minutes=50)
    result = runner.run(planning(1, max_walk_minutes=40), ledger=ServiceBudget())
    assert result["status"] == "invalid_plan"
    assert any(
        c["rule"] == "daily_walking_limit" and c["status"] == "fail"
        for c in result["plan"]["checks"]
    )
    assert len([a for a in result["plan"]["activities"] if a["kind"] == "sightseeing"]) == 1


def test_format_failure_keeps_sourced_partial_draft_and_records_degradation():
    runner, _ = make_product(bad_model=True)
    result = runner.run(planning(2), ledger=ServiceBudget())
    assert result["status"] == "draft" and "保守初稿" in result["plan"]["draft_note"]
    assert any(e["status"] == "failed" for e in result["events"])
    assert "invalid" not in json.dumps(result["plan"]["activities"])


def test_provider_failure_never_falls_back_to_invented_activities():
    runner, counts = make_product(source_error=True)
    result = runner.run(planning(), ledger=ServiceBudget())
    assert result["status"] == "stopped" and result["issue"] == "rate_limited"
    assert result["plan"] is None and counts["source"] == 1
    assert result["service_cost"]["status"] == "uncertain"


def test_excluded_and_required_places_remain_hard_constraints():
    runner, _ = make_product()
    result = runner.run(
        planning(1, excluded_places=("测试景点0",), required_places=("不存在的景点",)),
        ledger=ServiceBudget(),
    )
    assert result["status"] == "invalid_plan"
    assert all("测试景点0" != a["place_name"] for a in result["plan"]["activities"])
    assert any(
        c["rule"] == "required_place" and c["status"] == "fail" for c in result["plan"]["checks"]
    )


def test_unknown_usage_blocks_other_spending_and_retains_reservation():
    runner, _ = make_product()
    runner.model_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"model": "deepseek-flash", "usage": {}, "choices": []}
        )
    )
    result = runner.run(planning(), ledger=ServiceBudget())
    assert result["status"] == "stopped" and result["issue"] == "service_cost_unknown"
    assert result["service_cost"]["reserved_unsettled"] > 0


def test_cancel_before_sources_makes_no_paid_call():
    runner, counts = make_product()
    result = runner.run(planning(), ledger=ServiceBudget(), cancel=lambda: True)
    assert result["issue"] == "cancelled" and counts == {"source": 0, "model": 0}


def test_real_pacing_clock_boundary_never_calls_sleep_with_a_negative_duration(monkeypatch):
    from travel_agent.run_context import RunContext
    from travel_agent.source_models import PlaceQuery, SourceBatch

    clock = iter([99.999, 99.999, 100.001, 100.001])
    monkeypatch.setattr("travel_agent.run_context.monotonic", lambda: next(clock, 100.001))
    monkeypatch.setattr(RunContext, "amap_next_at", 100.0)
    sleeps = []
    monkeypatch.setattr("travel_agent.run_context.sleep", lambda value: sleeps.append(value))
    batch = SourceBatch(
        run_id="test",
        provider="amap",
        operation="places",
        retrieved_at="2026-11-11T00:00:00Z",
        query_sha256="test",
        requested_count=1,
        received_count=0,
        status="empty",
        hits=(),
        pagination_status="end",
        elapsed_ms=0,
    )
    monkeypatch.setattr("travel_agent.run_context.find_places", lambda *args, **kwargs: batch)
    ctx = RunContext(
        DeepSeekSettings("test"),
        "amap",
        PriceCard(amap_free_quota_only=True),
        ServiceBudget(),
        today=TODAY,
        start=0,
    )
    assert ctx.source(PlaceQuery(city="南京", keywords="测试")).status == "empty"
    assert sleeps and all(value >= 0 for value in sleeps)


def test_scheduler_never_shortens_required_visit_to_fit_window():
    a, b = ("amap_" + fingerprint(x)[:20] for x in ("a", "b"))
    route = WalkingPath(source_id="walk", distance_meters=1000, duration_seconds=3600)
    activities, checks, walking = arrange(
        [Stop(a, 1, 120, 540, required=True), Stop(b, 1, 120, 690, required=True)],
        DayWindow(end_minute=780),
        {(a, b): route},
    )
    assert len(activities) == 1 and activities[0].end_minute - activities[0].start_minute == 120
    assert checks[0]["rule"] == "required_activity_fit" and checks[0]["status"] == "fail"
    assert walking[1] == 0
