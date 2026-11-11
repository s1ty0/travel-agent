from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_planner import A, B, call, data, default_rounds, make_runner, proposal

from travel_agent.day_schedule import build_day_schedule
from travel_agent.plan_render import render_markdown
from travel_agent.planning_models import DayWindow, PlannerLimits, PlanningInput
from travel_agent.source_models import WalkingPath


def activities():
    return [
        {
            "day": 1,
            "place_source_id": A,
            "start_minute": 540,
            "end_minute": 690,
            "kind": "sightseeing",
        },
        {
            "day": 1,
            "place_source_id": B,
            "start_minute": 780,
            "end_minute": 900,
            "kind": "sightseeing",
        },
    ]


def test_early_afternoon_finish_exposes_lunch_dinner_and_evening_without_changing_visits():
    visits = activities()
    original = deepcopy(visits)
    frames = build_day_schedule(visits, 2, DayWindow().model_dump(), {})
    first = frames[0]
    assert any(
        block["title"] == "午餐及转场" for block in first["blocks"] if block["kind"] != "activity"
    )
    dinner = next(block for block in first["blocks"] if block.get("title") == "晚餐 · 餐厅待选")
    assert (dinner["start_minute"], dinner["end_minute"]) == (1080, 1140)
    assert first["blocks"][-1]["end_minute"] == 1200
    assert "返程" in first["blocks"][-1]["title"]
    assert frames[1]["missing_day"] and frames[1]["blocks"][0]["kind"] == "unplanned_day"
    assert visits == original and first["has_open_time"]


def test_explicit_15h_end_does_not_get_forced_evening_or_dinner():
    frame = build_day_schedule(activities(), 1, {"start_minute": 540, "end_minute": 900}, {})[0]
    assert max(block["end_minute"] for block in frame["blocks"]) == 900
    assert "晚餐" not in frame["missing_meals"]
    assert not any("晚" in block.get("title", "") for block in frame["blocks"])


def test_no_free_mealtime_is_reported_instead_of_overlapping_the_visit():
    visit = [{**activities()[0], "end_minute": 1200}]
    frame = build_day_schedule(visit, 1, DayWindow().model_dump(), {})[0]
    assert frame["missing_meals"] == ["午餐", "晚餐"]
    assert len(frame["blocks"]) == 1 and frame["blocks"][0]["kind"] == "activity"


def test_known_transfer_budget_is_reserved_before_suggesting_a_meal():
    route = WalkingPath(source_id="walk", distance_meters=1000, duration_seconds=5400)
    frame = build_day_schedule(activities(), 1, DayWindow().model_dump(), {(A, B): route})[0]
    gap = next(block for block in frame["blocks"] if block["kind"] == "transfer_rest")
    assert "100分钟" in gap["note"] and "午餐" in frame["missing_meals"]


def test_same_place_gap_does_not_invent_a_transfer():
    visits = activities()
    visits[1]["place_source_id"] = A
    frame = build_day_schedule(visits, 1, DayWindow().model_dump(), {})[0]
    assert not any(block["kind"] in {"meal_transfer", "transfer_rest"} for block in frame["blocks"])


def test_coverage_failure_stays_failed_and_export_preserves_missing_day():
    rounds = [default_rounds()[0], [call("submit_plan", {"activities": activities()}, "partial")]]
    runner, counts = make_runner(rounds)
    request = PlanningInput(city="南京", trip=data(days=2).trip, day_window=DayWindow())
    result = runner.run(request)
    assert result["status"] == "invalid_plan"
    coverage = next(check for check in result["plan"]["checks"] if check["rule"] == "day_coverage")
    assert coverage["missing_days"] == [2] and coverage["expected_days"] == 2
    assert result["plan"]["day_schedule"][1]["missing_day"]
    assert counts["source"] == 3
    assert "晚间自由活动或返程" in render_markdown(result)
    assert "这一天尚未安排" in render_markdown(result)


def test_window_violation_can_be_repaired_by_time_patch_without_changing_duration():
    from travel_agent.planning_models import Proposal
    from travel_agent.time_patch import proposal_hash

    initial = proposal()
    initial["activities"] = initial["activities"][:1]
    initial["activities"][0].update(start_minute=480, end_minute=540)
    rounds = [
        default_rounds()[0],
        [call("submit_plan", initial, "early")],
        [
            call(
                "patch_plan_times",
                {
                    "base_proposal_sha256": proposal_hash(Proposal.model_validate(initial)),
                    "shifts": [{"activity_index": 0, "start_minute": 540}],
                },
                "shift",
            )
        ],
    ]
    runner, _ = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(PlanningInput(city="南京", trip=data().trip, day_window=DayWindow()))
    assert result["status"] == "draft" and result["replans"] == 1
    assert result["validation_attempts"][0]["failed_rules"] == ["activity_time_window"]
    assert result["validation_attempts"][-1]["repair_mode"] == "shift_times_only"


@pytest.mark.parametrize(
    "window",
    [{"start_minute": 1000, "end_minute": 900}, {"end_minute": True}, {"start_minute": -1}],
)
def test_invalid_daily_window_is_rejected(window):
    with pytest.raises(ValidationError):
        DayWindow(**window)


def test_schedule_audit_keeps_version_and_counts_without_activity_times(tmp_path):
    import json
    import sqlite3
    from contextlib import closing

    from travel_agent.planner_cli import save_run

    runner, _ = make_runner(
        [default_rounds()[0], [call("submit_plan", {"activities": activities()}, "one")]]
    )
    result = runner.run(PlanningInput(city="南京", trip=data(days=2).trip, day_window=DayWindow()))
    path = tmp_path / "audit.sqlite3"
    save_run(result, path)
    with closing(sqlite3.connect(path)) as db:
        metadata = json.loads(db.execute("SELECT metadata_json FROM planning_runs").fetchone()[0])
    assert metadata["day_schedule_summary"]["version"] == "day-frame-v1"
    assert metadata["day_schedule_summary"]["missing_days"] == 1
    assert "activity_index" not in json.dumps(metadata) and "start_minute" not in json.dumps(
        metadata
    )


def test_model_provided_meal_counts_as_lunch_without_an_extra_overlapping_block():
    visits = [
        {"day": 1, "kind": "meal", "place_source_id": A, "start_minute": 720, "end_minute": 780}
    ]
    frame = build_day_schedule(visits, 1, DayWindow().model_dump(), {})[0]
    assert "午餐" not in frame["missing_meals"]
    assert not any("午餐" in block.get("title", "") for block in frame["blocks"])


def test_web_finalizes_early_and_reserves_one_round_for_missing_day_repair():
    first = proposal()
    first["activities"] = first["activities"][:1]
    fixed = proposal()
    fixed["activities"][1]["day"] = 2
    rounds = [
        default_rounds()[0],
        [call("submit_plan", first, "first")],
        [call("submit_plan", fixed, "fixed")],
    ]
    runner, counts = make_runner(rounds, limits=PlannerLimits(allow_rule_draft=True, max_replans=1))
    result = runner.run(
        PlanningInput(city="南京", trip=data(days=2).trip, day_window=DayWindow()),
        initial_model_calls=1,
    )
    assert result["status"] == "draft" and result["replans"] == 1
    assert result["model_calls"] == 4 and counts["model"] == 3
    assert all(not frame["missing_day"] for frame in result["plan"]["day_schedule"])
    tools = counts["payloads"][1]["tools"]
    assert [tool["function"]["name"] for tool in tools] == ["submit_plan"]
    schema = tools[0]["function"]["parameters"]
    assert schema["$defs"]["Activity"]["properties"]["place_source_id"]["enum"] == sorted([A, B])
    assert schema["$defs"]["Activity"]["properties"]["day"]["enum"] == [1, 2]


def test_query_after_early_finalization_is_not_executed_even_with_spare_quota():
    rounds = default_rounds()
    runner, counts = make_runner(
        rounds, limits=PlannerLimits(allow_rule_draft=True, max_model_calls=3)
    )
    result = runner.run(data())
    assert result["status"] == "draft" and counts["source"] == 3
    deferred = [event for event in result["events"] if event.get("status") == "not_executed"]
    assert len(deferred) == 1 and deferred[0]["issue"] == "finalization_required"


def test_discarded_malformed_query_does_not_block_final_submission():
    rounds = [
        default_rounds()[0],
        [call("find_places", {"unexpected-private-field": "private-value"}, "ignored")],
        [call("submit_plan", proposal(), "final")],
    ]
    runner, counts = make_runner(rounds, limits=PlannerLimits(allow_rule_draft=True))
    result = runner.run(data(), initial_model_calls=1)
    assert result["status"] == "draft" and counts["source"] == 3
    assert any(event.get("issue") == "finalization_required" for event in result["events"])


def test_invalid_model_arguments_report_schema_fields_without_rejected_values():
    import json

    runner, counts = make_runner(
        [[call("find_places", {"keywords": 123, "private-field": "private-value"}, "bad")]]
    )
    result = runner.run(data())
    assert result["issue"] == "invalid_model_or_tool_response" and counts["source"] == 0
    assert result["events"][-1]["errors"] == [
        {"field": "keywords", "code": "string_type"},
        {"field": "unknown_field", "code": "extra_forbidden"},
    ]
    assert "private-field" not in json.dumps(result) and "private-value" not in json.dumps(result)
