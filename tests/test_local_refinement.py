import pytest
from test_product_planner import make_product, planning

from travel_agent.deepseek import ProviderError
from travel_agent.refine_runner import refine_product
from travel_agent.refinement import parse_local_edit
from travel_agent.service_budget import ServiceBudget


def base_case(*, route_minutes=7):
    runner, counts = make_product(route_minutes=route_minutes)
    data = planning(2, max_walk_minutes=90)
    result = runner.run(data, ledger=ServiceBudget())
    assert result["status"] == "draft"
    return runner, counts, data, {"planning": data.model_dump(mode="json"), "result": result}


def test_day_scoped_end_time_preserves_other_day_and_makes_no_model_call():
    runner, counts, data, base = base_case()
    edit = parse_local_edit("第二天下午五点结束", data, base)
    assert edit.affected_days == (2,)
    before = counts.copy()
    result = refine_product(runner, edit.planning, base, edit, ServiceBudget())
    assert result["status"] == "draft" and result["model_calls"] == result["source_calls"] == 0
    assert counts == before
    original = [a for a in base["result"]["plan"]["activities"] if a["day"] == 1]
    actual = [a for a in result["plan"]["activities"] if a["day"] == 1]
    assert actual == original
    day_two = result["plan"]["day_schedule"][1]
    assert day_two["blocks"][-1]["end_minute"] == 1020
    assert all(a["end_minute"] <= 1020 for a in result["plan"]["activities"] if a["day"] == 2)
    assert result["service_cost"]["estimated_upper"] == 0


def test_less_walking_removes_only_optional_sight_on_target_day():
    runner, _, data, base = base_case(route_minutes=20)
    edit = parse_local_edit("第二天少走路", data, base)
    assert edit.planning.day_overrides[0].max_walk_minutes == 45
    result = refine_product(runner, edit.planning, base, edit, ServiceBudget())
    assert result["status"] == "draft" and result["model_calls"] == 0
    sights = [a for a in result["plan"]["activities"] if a["kind"] == "sightseeing"]
    assert sum(a["day"] == 1 for a in sights) == 2 and sum(a["day"] == 2 for a in sights) == 1
    assert result["plan"]["walking_minutes_by_day"][2] <= 45


def test_remove_an_exact_owned_place_keeps_evidence_and_does_not_invent_replacement():
    runner, _, data, base = base_case()
    place = next(
        a
        for a in base["result"]["plan"]["activities"]
        if a["day"] == 2 and a["kind"] == "sightseeing"
    )
    edit = parse_local_edit("第二天换掉" + place["place_name"], data, base)
    result = refine_product(runner, edit.planning, base, edit, ServiceBudget())
    assert not any(
        a["place_source_id"] == place["place_source_id"] for a in result["plan"]["activities"]
    )
    assert result["model_calls"] == 0 and "替代地点需要另行选择" in result["plan"]["draft_note"]


def test_budget_only_edit_reuses_evidence_and_preserves_unknown_expenses():
    runner, _, data, base = base_case()
    edit = parse_local_edit("预算改为2000～3000", data, base)
    assert edit.planning.trip.budget == "2000～3000" and not edit.planning.trip.cap_confirmed
    result = refine_product(runner, edit.planning, base, edit, ServiceBudget())
    assert result["status"] == "draft" and result["source_calls"] == result["model_calls"] == 0
    assert result["plan"]["travel_budget"]["status"] == "uncertain"


@pytest.mark.parametrize(
    "command,issue",
    [
        ("第三天少走路", "day_override_outside_trip"),
        ("第二天去一个更好的地方", "unsupported_local_refinement"),
        ("换掉不存在的地方", "ambiguous_place_change"),
    ],
)
def test_ambiguous_or_out_of_scope_local_edits_stop_before_provider_calls(command, issue):
    _, counts, data, base = base_case()
    before = counts.copy()
    with pytest.raises(ProviderError, match=issue):
        parse_local_edit(command, data, base)
    assert counts == before


def test_unknown_route_never_passes_daily_walking_limit():
    from travel_agent.planning_models import DayWindow
    from travel_agent.scheduler import Stop, arrange

    _, checks, _ = arrange(
        [Stop("a", 1), Stop("b", 1, preferred=840)], DayWindow(), {}, max_walk_minutes=90
    )
    assert next(c for c in checks if c["rule"] == "daily_walking_limit")["status"] == "unknown"


def test_structured_budget_change_can_reuse_the_prior_plan():
    from travel_agent.refinement import structured_edit

    runner, _, data, base = base_case()
    changed = data.model_copy(update={"trip": data.trip.model_copy(update={"budget": "2000以内"})})
    edit = structured_edit(changed, data)
    assert edit is not None
    result = refine_product(runner, changed, base, edit, ServiceBudget())
    assert result["status"] == "draft" and result["model_calls"] == result["source_calls"] == 0
    moved = changed.model_copy(update={"city": "杭州"})
    assert structured_edit(moved, data) is None
