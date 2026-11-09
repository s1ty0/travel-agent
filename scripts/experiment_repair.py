"""Hybrid experiment: scripted initial conflicts and sources, an opt-in live repair model only."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from evaluate_planning import FIXTURE, ROOT, A, B, run_case, tool

from travel_agent.deepseek import DeepSeekSettings
from travel_agent.service_budget import PriceCard

EXPERIMENT_VERSION = "hybrid-repair-v2"
SCENARIOS = {
    "count": "explicit_single_sight_repaired",
    "transfer": "insufficient_transfer_repaired",
    "overlap": "overlap_repair_exhausted",
    "budget_guard": "repair_cannot_reset_budget",
}


def experiment_cases():
    raw = FIXTURE.read_bytes()
    manifest = json.loads(FIXTURE.with_suffix(".sha256.json").read_text("utf-8"))
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("frozen_acceptance_fixture_changed")
    return {item["id"]: item for item in json.loads(raw)["cases"]}


def offline_repair(request):
    """A dry-run response, explicitly scripted; never reported as a live-model success."""
    payload = json.loads(request.content)
    feedback = json.loads(payload["messages"][-1]["content"])
    single = feedback["effective_constraints"]["max_sightseeing_places"] == 1
    original = initial_activities(payload["messages"])
    activities = original[:1] if single else original
    cursor = 540
    for item in activities:
        duration = item["end_minute"] - item["start_minute"]
        item["start_minute"] = cursor
        item["end_minute"] = cursor + duration
        cursor = item["end_minute"] + 30
    return httpx.Response(
        200,
        json={
            "model": payload["model"],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            tool("submit_plan", {"activities": activities}, "experiment-fixed")
                        ]
                    },
                }
            ],
        },
    )


def inspect_request(request):
    if str(request.url) != "https://api.deepseek.com/chat/completions":
        raise ValueError("unexpected_experiment_endpoint")
    payload = json.loads(request.content)
    names = [item["function"]["name"] for item in payload["tools"]]
    if names != ["submit_plan"] or payload["tool_choice"]["function"]["name"] != "submit_plan":
        raise ValueError("experiment_requires_submit_only")
    feedback = json.loads(payload["messages"][-1]["content"])
    if payload["messages"][-1]["role"] != "tool" or feedback["status"] != "revision_requested":
        raise ValueError("experiment_requires_real_validation_feedback")
    return payload


def initial_activities(messages):
    for message in messages:
        for call in message.get("tool_calls", []):
            if call["function"]["name"] == "submit_plan":
                return json.loads(call["function"]["arguments"])["activities"]
    return []


def scope_inventory(activities):
    # Compare against actual seeded durations instead of assuming every visit lasts 60 minutes.
    return Counter(
        (
            item["day"],
            item["place_source_id"],
            item.get("kind", "sightseeing"),
            item["end_minute"] - item["start_minute"],
        )
        for item in activities
    )


def summarize(scenario, result, counters, calls, live, elapsed_ms, original):
    plan = result.get("plan") or {}
    attempts = result.get("validation_attempts", [])
    activities = plan.get("activities", [])
    selected = {item["place_source_id"] for item in activities if item["kind"] == "sightseeing"}
    failures = [item["rule"] for item in plan.get("checks", []) if item["status"] == "fail"]
    checks = {
        "initial_conflict_recorded": bool(attempts and attempts[0]["status"] == "invalid_plan"),
        "one_repair_requested": result.get("replans") == 1,
        "single_candidate_request": calls == (0 if scenario == "budget_guard" else 1),
        "same_ledger_cap": result["service_cost"]["cap"]
        == (260000 if scenario == "budget_guard" else 2000000),
        "unknowns_preserved": plan.get("facts_verified") is False
        and (plan.get("travel_budget") or {}).get("status") == "uncertain",
    }
    if scenario == "budget_guard":
        checks.update(
            blocked_before_candidate=result.get("issue") == "service_budget_exhausted"
            and result["status"] == "stopped",
            last_failure_preserved=len(attempts) == 1 and "transfer_duration" in failures,
        )
    else:
        checks.update(
            final_draft_valid=result["status"] == "draft" and not failures,
            new_validation_recorded=len(attempts) == 2,
            source_count_unchanged=len(attempts) == 2
            and attempts[0]["source_calls"] == attempts[1]["source_calls"],
            experiment_scope_preserved=selected <= {A, B}
            and len(selected) == 1
            and len(activities) == 1
            and all(
                count <= scope_inventory(original)[item]
                for item, count in scope_inventory(activities).items()
            )
            if scenario == "count"
            else selected == {A, B}
            and len(activities) == 2
            and scope_inventory(activities) == scope_inventory(original),
        )
    # The seeded model/search costs are simulated, so do not label the engine total an account bill.
    seeded_model_micro = 24600 if scenario == "budget_guard" else 1200
    observed_candidate_micro = result["service_cost"]["estimated_upper"] - 5000 - seeded_model_micro
    return {
        "scenario": scenario,
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "status": result["status"],
        "issue": result.get("issue"),
        "mode": "hybrid_live_model_synthetic_context" if live else "fully_scripted_dry_run",
        "live_model_calls": calls if live else 0,
        "scripted_initial_model_calls": 2,
        "scripted_source_calls": counters["sources"],
        "live_source_calls": 0,
        "replans": result.get("replans"),
        "validation_attempts": attempts,
        "elapsed_ms": elapsed_ms,
        "planning_ledger_estimate_includes_simulated_charges": result["service_cost"],
        "observed_live_model_upper_micro_cny": (
            None
            if live and calls and result["service_cost"]["status"] == "uncertain"
            else observed_candidate_micro
            if live and calls
            else 0
        ),
        "simulated_initial_model_and_search_micro_cny": seeded_model_micro + 5000,
        "actual_billed": None,
        "final_failed_rules": failures,
        "facts_verified": False,
        "full_prompt_or_plan_saved": False,
        "scope_check_basis": "actual_seeded_activity_types_places_and_durations",
    }


def run_experiment(*, live=False, selected="all", candidate_transport=None, output=None):
    if output is not None and output.exists():
        raise ValueError("experiment_report_already_exists")
    cases = experiment_cases()
    settings = DeepSeekSettings.from_env() if live else DeepSeekSettings("synthetic-experiment-key")
    today = datetime.now(timezone(timedelta(hours=8))).date()
    # Validate prices before starting a live experiment, including before scripted setup.
    if live:
        PriceCard().validate(settings.model, today)
    outcomes = []
    for name, case_id in SCENARIOS.items():
        if selected not in ("all", name):
            continue
        calls = 0
        backend = httpx.HTTPTransport(retries=0) if live and candidate_transport is None else None

        def candidate(request, backend=backend):
            nonlocal calls
            inspect_request(request)
            calls += 1
            if candidate_transport is not None:
                return candidate_transport.handle_request(request)
            if not live:
                return offline_repair(request)
            # Only the correction request reaches the network, no retries or target-site access.
            return backend.handle_request(request)

        try:
            result, counters, payloads, elapsed = run_case(
                cases[case_id],
                repair_transport=httpx.MockTransport(candidate),
                settings=settings,
                today=today if live else None,
            )
        finally:
            if backend is not None:
                backend.close()
        outcomes.append(
            summarize(
                name,
                result,
                counters,
                calls,
                live,
                elapsed,
                initial_activities(payloads[-1]["messages"]),
            )
        )
    report = {
        "version": EXPERIMENT_VERSION,
        "experiment_id": str(uuid4()),
        "model": settings.model,
        "mode": "hybrid_live_model_synthetic_context" if live else "fully_scripted_dry_run",
        "passed": bool(outcomes) and all(item["passed"] for item in outcomes),
        "production_release_ready": False,
        "independent_real_world_evaluation": False,
        "generated_initial_failure_by_live_model": False,
        "live_model_calls": sum(item["live_model_calls"] for item in outcomes),
        "live_source_calls": 0,
        "cases": outcomes,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            target.write(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="受控混合修正实验；默认不联网，--live仅真实调用修正模型"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = args.output or ROOT / "artifacts/qa" / (
        ("hybrid-repair-live-" if args.live else "hybrid-repair-dry-") + str(uuid4()) + ".json"
    )
    try:
        report = run_experiment(live=args.live, selected=args.scenario, output=path)
    except Exception:
        # Research CLI errors must not echo inputs, credentials, or upstream bodies.
        print('{"error":"repair_experiment_failed"}')
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "mode": report["mode"],
                "live_model_calls": report["live_model_calls"],
                "cases": [
                    {
                        "scenario": item["scenario"],
                        "passed": item["passed"],
                        "checks": item["checks"],
                        "status": item["status"],
                    }
                    for item in report["cases"]
                ],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)
