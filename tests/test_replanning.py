import io
import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from pydantic import ValidationError
from test_planner import A, B, call, data, default_rounds, make_runner, proposal

from travel_agent.parsing import intake
from travel_agent.plan_render import render_markdown
from travel_agent.planner import validate_proposal
from travel_agent.planning_models import PlannerLimits, PlanningInput, Proposal
from travel_agent.source_models import SourceBatch
from travel_agent.time_patch import proposal_hash


def patch_call(original, *, start=650):
    return call(
        "patch_plan_times",
        {
            "base_proposal_sha256": proposal_hash(Proposal.model_validate(original)),
            "shifts": [{"activity_index": 1, "start_minute": start}],
        },
        "fixed",
    )


def repairing_rounds():
    initial = proposal()
    initial["activities"][1]["start_minute"] = 605
    initial["activities"][1]["end_minute"] = 665
    return [
        default_rounds()[0],
        [call("submit_plan", initial, "first")],
        [patch_call(initial)],
    ]


@pytest.mark.parametrize("value", [0, 10, True, "1"])
def test_structured_sightseeing_limit_is_strict(value):
    with pytest.raises(ValidationError):
        PlanningInput(city="南京", trip=data().trip, constraints={"max_sightseeing_places": value})


@pytest.mark.parametrize(
    "preference",
    [
        "不只去一处景点",
        "只去一处餐厅",
        "如果下雨只去一处景点",
        "别人说只去一处景点",
        "只去两处景点",
    ],
)
def test_unmatched_natural_language_is_not_silently_treated_as_a_hard_limit(preference):
    request = data(preferences=(preference,))
    assert request.effective_constraints()["max_sightseeing_places"] is None
    assert request.effective_constraints()["other_preferences"] == "not_fully_validated"


def test_explicit_and_literal_limits_are_intersected_not_relaxed():
    request = PlanningInput(
        city="南京",
        trip=data(preferences=("只安排1个景点。",)).trip,
        constraints={"max_sightseeing_places": 3},
    )
    assert request.effective_constraints()["max_sightseeing_places"] == 1
    assert request.effective_constraints()["explicit_limit"] == 3
    assert request.effective_constraints()["literal_single_sight_preference"] is True


def test_sightseeing_place_count_excludes_meals_rest_and_repeated_visit_to_same_id():
    runner, _ = make_runner()
    result = runner.run(data())
    batches = tuple(SourceBatch.model_validate(batch) for batch in result["sources"])
    activities = [
        {"day": 1, "place_source_id": A, "start_minute": 540, "end_minute": 600},
        {"day": 1, "place_source_id": A, "start_minute": 600, "end_minute": 630},
        {"day": 1, "place_source_id": B, "start_minute": 690, "end_minute": 720, "kind": "meal"},
    ]
    checked = validate_proposal(
        Proposal(activities=activities),
        intake(data().trip),
        batches,
        {},
        constraints={"max_sightseeing_places": 1},
    )
    count = next(c for c in checked["checks"] if c["rule"] == "sightseeing_place_limit")
    assert count["status"] == "pass" and count["actual"] == 1
    assert checked["travel_budget"]["status"] == "uncertain"


def test_repair_uses_original_tool_history_only_submit_tool_and_same_sources():
    runner, counts = make_runner(repairing_rounds(), limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    assert result["status"] == "draft" and result["replans"] == 1
    assert result["source_calls"] == 3 and result["model_calls"] == 3
    assert result["service_cost"]["estimated_upper"] == 6800
    assert [item["status"] for item in result["validation_attempts"]] == ["invalid_plan", "draft"]
    repair_payload = counts["payloads"][2]
    assert [tool["function"]["name"] for tool in repair_payload["tools"]] == ["patch_plan_times"]
    feedback = json.loads(repair_payload["messages"][-1]["content"])
    assert repair_payload["messages"][-1]["tool_call_id"] == "first"
    assert feedback["status"] == "revision_requested"
    assert feedback["failed_checks"][0]["required_minutes"] == 30
    assert feedback["failed_checks"][0]["available_minutes"] == 5
    assert (
        result["validation_attempts"][0]["proposal_sha256"]
        != result["validation_attempts"][1]["proposal_sha256"]
    )


@pytest.mark.parametrize("tool", ["find_places", "search_web", "walking_route"])
def test_model_cannot_request_more_source_tools_during_repair(tool):
    rounds = repairing_rounds()
    rounds[2] = [call(tool, {}, "extra")]
    runner, counts = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    assert result["status"] == "stopped" and result["issue"] == "repair_patch_only"
    assert result["plan"]["status"] == "invalid_plan"
    assert counts["source"] == 3 and len(result["validation_attempts"]) == 1
    assert "修正过程已停止" in render_markdown(result)


def test_model_error_during_repair_preserves_invalid_attempt_and_unsettled_cost():
    runner, counts = make_runner(repairing_rounds(), limits=PlannerLimits(max_replans=1))
    original = runner.model_transport

    def wrapped(request):
        if counts["model"] >= 2:
            return httpx.Response(200, json={"model": "deepseek-flash", "usage": {}})
        return original.handle_request(request)

    runner.model_transport = httpx.MockTransport(wrapped)
    result = runner.run(data())
    assert result["status"] == "stopped" and result["issue"] == "model_usage_or_price_unknown"
    assert result["plan"]["status"] == "invalid_plan" and result["replans"] == 1
    assert result["service_cost"]["status"] == "uncertain"
    assert result["service_cost"]["reserved_unsettled"] > 0
    assert len(result["validation_attempts"]) == 1


def test_last_allowed_model_call_cannot_start_a_new_repair():
    runner, counts = make_runner(
        repairing_rounds(), limits=PlannerLimits(max_replans=1, max_model_calls=2)
    )
    result = runner.run(data())
    assert result["status"] == "invalid_plan" and result["replans"] == 0
    assert counts["model"] == 2 and len(result["validation_attempts"]) == 1


def test_repair_limit_default_is_zero_and_rejects_unbounded_configuration():
    assert PlannerLimits().max_replans == 0
    for value in (2, -1, True):
        with pytest.raises(ValidationError):
            PlannerLimits(max_replans=value)


def test_validation_audit_keeps_failures_without_names_times_or_prompt(tmp_path):
    from travel_agent.planner_cli import save_run

    runner, _ = make_runner(repairing_rounds(), limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    path = tmp_path / "repair.sqlite3"
    save_run(result, path)
    with closing(sqlite3.connect(path)) as db:
        raw = db.execute("SELECT metadata_json FROM planning_runs").fetchone()[0]
    stored = json.loads(raw)
    assert len(stored["validation_attempts"]) == 2 and stored["replans"] == 1
    assert "transfer_duration" in stored["validation_attempts"][0]["failed_rules"]
    assert all(
        text not in raw for text in ("测试地点", "activities", "预算2000", "revision_requested")
    )


def test_cli_exposes_explicit_bounded_repair_switch(monkeypatch, capsys, tmp_path):
    from travel_agent.planner_cli import main

    runner, _ = make_runner()
    selected = {}

    def capture(*args, **kwargs):
        selected["max_replans"] = kwargs["limits"].max_replans
        return runner

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-cli-key")
    monkeypatch.setenv("AMAP_WEB_API_KEY", "synthetic-cli-map")
    monkeypatch.setattr("travel_agent.planner_cli.BaselineRunner", capture)
    monkeypatch.setattr("sys.argv", ["plan", "--live", "--max-replans", "1"])
    monkeypatch.setattr("sys.stdin", io.StringIO(data().model_dump_json()))
    assert main() == 0 and selected["max_replans"] == 1
    assert json.loads(capsys.readouterr().out)["status"] == "draft"


@pytest.mark.parametrize("change", ["drop", "duration", "kind", "day", "citation", "place"])
def test_time_only_repair_cannot_change_original_non_time_scope(change):
    rounds = repairing_rounds()
    fixed = proposal()
    if change == "drop":
        fixed["activities"] = fixed["activities"][:1]
    elif change == "duration":
        fixed["activities"][1]["end_minute"] -= 10
    elif change == "kind":
        fixed["activities"][1]["kind"] = "meal"
    elif change == "day":
        fixed["activities"][1]["day"] = 2
    elif change == "citation":
        fixed["activities"][0]["citations"] = []
    elif change == "place":
        fixed["activities"][1]["place_source_id"] = A
    rounds[2] = [call("submit_plan", fixed, "changed")]
    runner, counts = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    assert result["status"] == "stopped" and result["issue"] == "repair_patch_only"
    assert len(result["validation_attempts"]) == 1
    assert result["plan"]["status"] == "invalid_plan"
    assert counts["source"] == 3


def test_overlap_feedback_includes_known_required_transfer_time():
    rounds = repairing_rounds()
    initial = proposal()
    initial["activities"][1].update(start_minute=590, end_minute=650)
    rounds[1] = [call("submit_plan", initial, "overlap")]
    rounds[2] = [patch_call(initial)]
    runner, counts = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    assert result["status"] == "draft"
    feedback = json.loads(counts["payloads"][2]["messages"][-1]["content"])
    check = next(item for item in feedback["failed_checks"] if item["rule"] == "time_overlap")
    assert check["available_minutes"] == -10 and check["required_minutes"] == 30
    assert feedback["repair_scope"]["mode"] == "shift_times_only"
    assert [item["duration_minutes"] for item in feedback["repair_scope"]["activities"]] == [60, 60]


@pytest.mark.parametrize("change", ["remove", "reclassify", "duplicate"])
def test_quantity_repair_can_remove_but_cannot_reclassify_or_duplicate(change):
    initial = proposal()
    fixed = proposal()
    if change == "remove":
        fixed["activities"] = fixed["activities"][:1]
    elif change == "reclassify":
        fixed["activities"][1]["kind"] = "meal"
    else:
        fixed["activities"] = [fixed["activities"][0], dict(fixed["activities"][0])]
        fixed["activities"][1].update(start_minute=650, end_minute=710)
    rounds = [
        default_rounds()[0],
        [call("submit_plan", initial, "quantity")],
        [call("submit_plan", fixed, "quantity-fixed")],
    ]
    request = PlanningInput.model_validate(
        {**data().model_dump(), "constraints": {"max_sightseeing_places": 1}}
    )
    runner, _ = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(request)
    assert result["validation_attempts"][-1]["repair_mode"] == "remove_or_shift_only"
    if change == "remove":
        assert result["status"] == "draft"
    else:
        assert result["status"] == "invalid_plan"
        assert "repair_scope_violation" in result["validation_attempts"][-1]["failed_rules"]
