"""Keep dry/live and scripted/observed charges separate in the controlled experiment."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def experiment(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "repair_experiment", ROOT / "scripts/experiment_repair.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dry_run_never_reads_credentials_or_creates_network_transport(
    experiment, monkeypatch, tmp_path
):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run must not create a network transport or read provider settings")

    monkeypatch.setattr(experiment.DeepSeekSettings, "from_env", forbidden)
    monkeypatch.setattr(httpx, "HTTPTransport", forbidden)
    output = tmp_path / "dry.json"
    report = experiment.run_experiment(output=output)
    assert report["passed"] and len(report["cases"]) == 4
    assert report["mode"] == "fully_scripted_dry_run"
    assert report["live_model_calls"] == report["live_source_calls"] == 0
    assert report["independent_real_world_evaluation"] is False
    assert all(item["observed_live_model_upper_micro_cny"] == 0 for item in report["cases"])
    assert json.loads(output.read_text("utf-8"))["experiment_id"] == report["experiment_id"]


def test_budget_case_prevents_candidate_callback_even_when_one_is_supplied(experiment):
    def forbidden(request):
        pytest.fail("exhausted shared ledger must stop before candidate transport")

    result = experiment.run_experiment(
        selected="budget_guard", candidate_transport=httpx.MockTransport(forbidden)
    )
    case = result["cases"][0]
    assert case["passed"] and case["status"] == "stopped"
    assert case["observed_live_model_upper_micro_cny"] == 0
    assert case["simulated_initial_model_and_search_micro_cny"] == 29600
    assert case["planning_ledger_estimate_includes_simulated_charges"]["estimated_upper"] == 29600


def test_candidate_receives_real_feedback_and_a_fixed_endpoint_only(experiment):
    captured = []

    def candidate(request):
        payload = experiment.inspect_request(request)
        captured.append(payload)
        assert payload["messages"][-1]["tool_call_id"] == "submit-1"
        return experiment.offline_repair(request)

    result = experiment.run_experiment(
        selected="transfer", candidate_transport=httpx.MockTransport(candidate)
    )
    assert result["passed"] and len(captured) == 1
    assert result["live_model_calls"] == 0
    assert captured[0]["messages"][-1]["content"].find("transfer_duration") >= 0


@pytest.mark.parametrize(
    "mode",
    ["unchanged", "drop_place", "shorten_visit", "reclassify", "buy", "unknown_usage", "timeout"],
)
def test_failed_candidates_never_count_as_success_or_trigger_second_attempt(experiment, mode):
    calls = []

    def candidate(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive upstream message", request=request)
        if mode == "unknown_usage":
            return httpx.Response(200, json={"model": "deepseek-flash", "usage": {}})
        response = experiment.offline_repair(request)
        body = json.loads(response.content)
        tool = body["choices"][0]["message"]["tool_calls"][0]
        args = json.loads(tool["function"]["arguments"])
        if mode == "unchanged":
            args["activities"][1].update(start_minute=605, end_minute=665)
        elif mode == "drop_place":
            args["activities"] = args["activities"][:1]
        elif mode == "shorten_visit":
            args["activities"][1]["end_minute"] -= 30
        elif mode == "reclassify":
            args["activities"][1]["kind"] = "meal"
        elif mode == "buy":
            tool["function"]["name"] = "pay"
        tool["function"]["arguments"] = json.dumps(args)
        return httpx.Response(200, json=body)

    report = experiment.run_experiment(
        selected="transfer", candidate_transport=httpx.MockTransport(candidate)
    )
    case = report["cases"][0]
    assert report["passed"] is False and case["passed"] is False
    assert len(calls) == 1 and case["replans"] == 1
    if mode in ("drop_place", "shorten_visit", "reclassify"):
        assert case["status"] == "invalid_plan"
        assert "repair_scope_violation" in case["final_failed_rules"]
        assert case["checks"]["experiment_scope_preserved"] is False
    assert "sensitive" not in json.dumps(report)


def test_reports_do_not_overwrite_old_experiment_or_read_keys_before_rejection(
    experiment, monkeypatch, tmp_path
):
    output = tmp_path / "old.json"
    output.write_text("old-experiment", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("existing output must be rejected before credentials are read")

    monkeypatch.setattr(experiment.DeepSeekSettings, "from_env", forbidden)
    with pytest.raises(ValueError, match="experiment_report_already_exists"):
        experiment.run_experiment(live=True, output=output)
    assert output.read_text("utf-8") == "old-experiment"


def test_request_guard_rejects_arbitrary_endpoint_or_missing_submit_feedback(experiment):
    with pytest.raises(ValueError, match="unexpected_experiment_endpoint"):
        experiment.inspect_request(httpx.Request("POST", "https://private.example/"))
    payload = {
        "tools": [{"function": {"name": "find_places"}}],
        "tool_choice": {"function": {"name": "submit_plan"}},
    }
    with pytest.raises(ValueError, match="experiment_requires_submit_only"):
        experiment.inspect_request(
            httpx.Request("POST", "https://api.deepseek.com/chat/completions", json=payload)
        )
