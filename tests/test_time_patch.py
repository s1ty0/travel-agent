"""Local time patches must preserve evidence, bind the base, and fail atomically."""

import json

import pytest
from pydantic import ValidationError
from test_planner import call, data, default_rounds, make_runner, proposal
from test_replanning import patch_call, repairing_rounds

from travel_agent.deepseek import ProviderError
from travel_agent.planning_models import PlannerLimits, Proposal
from travel_agent.time_patch import TimePatch, apply_time_patch, proposal_hash


def patch(original, **overrides):
    return {
        "base_proposal_sha256": proposal_hash(original),
        "shifts": [{"activity_index": 1, "start_minute": 650}],
        **overrides,
    }


def test_shift_preserves_every_other_field_duration_and_unlisted_activity():
    raw = proposal()
    raw["activities"][1].update(day=2, kind="rest")
    raw["activities"][0]["claims"] = [
        {
            "subject": "合成测试馆",
            "predicate": "reservation_required",
            "value": True,
            "source_id": "synthetic",
            "quote": "须预约",
        }
    ]
    original = Proposal.model_validate(raw)
    before = original.model_dump_json()
    result = apply_time_patch(
        original,
        TimePatch.model_validate(
            patch(
                original,
                shifts=[
                    {"activity_index": 0, "start_minute": 0},
                ],
            )
        ),
    )
    assert result.activities[0].start_minute == 0 and result.activities[0].end_minute == 60
    assert result.activities[0].model_dump(
        exclude={"start_minute", "end_minute"}
    ) == original.activities[0].model_dump(exclude={"start_minute", "end_minute"})
    assert result.activities[1] == original.activities[1]
    assert original.model_dump_json() == before


@pytest.mark.parametrize(
    "shifts",
    [
        [],
        [{"activity_index": True, "start_minute": 650}],
        [{"activity_index": 1, "start_minute": "650"}],
        [{"activity_index": 1, "start_minute": 650.0}],
        [{"activity_index": -1, "start_minute": 650}],
        [{"activity_index": 1, "start_minute": -1}],
        [{"activity_index": 1, "start_minute": 1440}],
        [{"activity_index": 1, "start_minute": 650, "end_minute": 710}],
        [{"activity_index": 1, "start_minute": 650}, {"activity_index": 1, "start_minute": 700}],
    ],
)
def test_invalid_patch_shape_and_duplicate_indices_are_rejected(shifts):
    with pytest.raises(ValidationError):
        TimePatch.model_validate(patch(Proposal.model_validate(proposal()), shifts=shifts))


@pytest.mark.parametrize(
    "problem,issue",
    [
        ("base", "time_patch_base_mismatch"),
        ("index", "time_patch_activity_unknown"),
        ("overflow", "time_patch_out_of_day"),
    ],
)
def test_bad_patch_stops_same_run_and_keeps_original_failure(problem, issue):
    rounds = repairing_rounds()
    original = Proposal.model_validate(json.loads(rounds[1][0]["function"]["arguments"]))
    args = patch(original)
    if problem == "base":
        args["base_proposal_sha256"] = "0" * 64
    elif problem == "index":
        args["shifts"][0]["activity_index"] = 8
    else:
        args["shifts"][0]["start_minute"] = 1439
    rounds[2] = [call("patch_plan_times", args, "bad-patch")]
    runner, counts = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    assert result["status"] == "stopped" and result["issue"] == issue
    assert result["plan"]["status"] == "invalid_plan"
    assert len(result["validation_attempts"]) == 1
    assert result["model_calls"] == 3 and result["replans"] == 1
    assert counts["source"] == 3


def test_multiple_shift_application_is_atomic_and_midnight_end_is_allowed():
    original = Proposal.model_validate(proposal())
    before = original.model_dump_json()
    good = TimePatch.model_validate(
        patch(original, shifts=[{"activity_index": 0, "start_minute": 1380}])
    )
    assert apply_time_patch(original, good).activities[0].end_minute == 1440
    bad = TimePatch.model_validate(
        patch(
            original,
            shifts=[
                {"activity_index": 0, "start_minute": 1380},
                {"activity_index": 8, "start_minute": 700},
            ],
        )
    )
    with pytest.raises(ProviderError, match="time_patch_activity_unknown"):
        apply_time_patch(original, bad)
    assert original.model_dump_json() == before


def test_patch_indices_follow_original_array_not_sorted_day_or_place():
    raw = proposal()
    raw["activities"].reverse()
    original = Proposal.model_validate(raw)
    updated = apply_time_patch(
        original,
        TimePatch.model_validate(
            patch(
                original,
                shifts=[
                    {"activity_index": 0, "start_minute": 900},
                ],
            )
        ),
    )
    assert updated.activities[0].place_source_id == original.activities[0].place_source_id
    assert updated.activities[0].start_minute == 900
    assert updated.activities[1] == original.activities[1]


@pytest.mark.parametrize("where", ["initial", "multiple", "still_overlapping"])
def test_patch_phase_single_submission_and_remaining_failure(where):
    rounds = repairing_rounds()
    if where == "initial":
        rounds = [[patch_call(proposal())]]
    elif where == "multiple":
        rounds[2] = [*rounds[2], {**rounds[2][0], "id": "second-patch"}]
    else:
        original = json.loads(rounds[1][0]["function"]["arguments"])
        rounds[2] = [patch_call(original, start=590)]
    runner, counts = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(data())
    if where == "initial":
        assert result["issue"] == "time_patch_not_enabled" and counts["source"] == 0
    elif where == "multiple":
        assert result["issue"] == "submit_must_be_separate"
        assert len(result["validation_attempts"]) == 1
    else:
        assert result["status"] == "invalid_plan" and counts["model"] == 3
        assert "time_overlap" in result["validation_attempts"][-1]["failed_rules"]
        assert result["validation_attempts"][-1]["time_patch_sha256"]


def test_day_coverage_repair_keeps_full_submission_and_complete_validation():
    initial = proposal()
    fixed = proposal()
    fixed["activities"][1]["day"] = 2
    rounds = [
        default_rounds()[0],
        [call("submit_plan", initial, "first")],
        [call("submit_plan", fixed, "fixed")],
    ]
    runner, counts = make_runner(rounds, limits=PlannerLimits(max_replans=1))
    result = runner.run(data(days=2))
    assert result["status"] == "draft"
    assert result["validation_attempts"][-1]["repair_mode"] == "rebuild_for_day_coverage"
    assert result["validation_attempts"][-1]["time_patch_sha256"] is None
    assert counts["payloads"][2]["tool_choice"]["function"]["name"] == "submit_plan"
