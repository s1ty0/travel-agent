"""Frozen synthetic acceptance using scripted providers and fixed expected outcomes."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from time import monotonic

import httpx

from travel_agent.deepseek import DeepSeekSettings
from travel_agent.planner import BaselineRunner
from travel_agent.planning_models import PlannerLimits, PlanningInput
from travel_agent.references import ReferenceInput, import_reference
from travel_agent.service_budget import PriceCard
from travel_agent.sources import fingerprint

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/planning_acceptance_v1.json"
A, B = ("amap_" + fingerprint(value)[:20] for value in ("acceptance-A", "acceptance-B"))
TITLE = "合成验收馆甲"


def tool(name, arguments, call_id):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def run_case(case, *, repair_transport=None, settings=None, today=None):
    scenario = case["scenario"]
    counters = {"models": 0, "sources": 0}
    payloads = []
    materials = []
    if scenario in ("expired_claim", "source_conflict"):
        text = (
            (
                "2026-11-01至2026-11-09，"
                if scenario == "expired_claim"
                else "2026-11-01至2026-11-30，"
            )
            + TITLE
            + "须预约。"
        )
        materials.append(ReferenceInput(title="合成规则甲", text=text, allow_model_use=True))
        if scenario == "source_conflict":
            materials.append(
                ReferenceInput(
                    title="合成规则乙",
                    text="2026-11-01至2026-11-30，" + TITLE + "无需预约。",
                    allow_model_use=True,
                )
            )

    def model(request):
        index = counters["models"]
        counters["models"] += 1
        payloads.append(json.loads(request.content))
        if index >= 2 and repair_transport is not None:
            return repair_transport.handle_request(request)
        if index == 0:
            calls = (
                [tool("pay", {}, "unauthorized")]
                if scenario == "injection"
                else [
                    tool("search_web", {"query": "南京 合成验收"}, "search"),
                    tool("find_places", {"keywords": "合成验收馆"}, "places"),
                ]
            )
        else:
            activities = [{"day": 1, "place_source_id": A, "start_minute": 540, "end_minute": 600}]
            two = scenario in (
                "count_repair",
                "count_no_repair",
                "transfer_repair",
                "overlap_unfixed",
                "ambiguous_route",
                "budget_stop",
            )
            if two and not (scenario == "count_repair" and index >= 2):
                start = 590 if scenario == "overlap_unfixed" else 605
                if scenario in ("count_repair", "count_no_repair", "ambiguous_route") or (
                    index >= 2 and scenario != "overlap_unfixed"
                ):
                    start = 650
                activities.append(
                    {
                        "day": 1,
                        "place_source_id": B,
                        "start_minute": start,
                        "end_minute": 765
                        if scenario in ("transfer_repair", "budget_stop") and index >= 2
                        else 720,
                    }
                )
            if materials:
                activities[0]["claims"] = [
                    {
                        "subject": TITLE,
                        "predicate": "reservation_required",
                        "value": True,
                        "source_id": import_reference(materials[0]).source_id,
                        "quote": "须预约",
                    }
                ]
            calls = [tool("submit_plan", {"activities": activities}, f"submit-{index}")]
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        if scenario == "budget_stop" and index == 1:
            usage = {"prompt_tokens": 10000, "completion_tokens": 500}
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": usage,
                "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": calls}}],
            },
        )

    def source(request):
        counters["sources"] += 1
        if request.url.path.endswith("/tools/search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://acceptance.example/visit",
                            "title": "合成验收资料",
                            "snippet": "仅作离线验收，非现实旅游事实。",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/place/text"):
            places = [
                {"id": "acceptance-A", "name": TITLE},
                {
                    "id": "acceptance-B",
                    "name": TITLE if scenario == "ambiguous_route" else "合成验收馆乙",
                },
            ]
            if scenario != "ambiguous_route":
                for place, coordinate in zip(places, ("118.8,32.0", "118.81,32.01"), strict=True):
                    place["location"] = coordinate
            return httpx.Response(200, json={"status": "1", "count": "2", "pois": places})
        if request.url.path.endswith("/direction/walking"):
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "route": {"paths": [{"distance": "1500", "duration": "1200"}]},
                },
            )
        raise AssertionError("unrecognized_scripted_provider_call")

    data = {"city": "南京", "trip": {"destination": "南京", "days": 1, "budget": "总预算2000以内"}}
    data.update({key: value for key, value in case["input"].items() if key != "trip"})
    data["trip"].update(case["input"].get("trip", {}))
    data["references"] = [material.model_dump(mode="json") for material in materials]
    options = {"service_cap_minor": case.get("service_cap_minor", 200)}
    if case["max_replans"] and "max_replans" in PlannerLimits.model_fields:
        options["max_replans"] = case["max_replans"]
    runner = BaselineRunner(
        settings or DeepSeekSettings("synthetic-acceptance-key"),
        "synthetic-amap-key",
        limits=PlannerLimits(**options),
        prices=PriceCard(amap_free_quota_only=True),
        today=today or date(2026, 11, 9),
        model_transport=httpx.MockTransport(model),
        source_transport=httpx.MockTransport(source),
    )
    start = monotonic()
    result = runner.run(PlanningInput.model_validate(data))
    return result, counters, payloads, round((monotonic() - start) * 1000)


def judge(case, result, counters):
    expected = case["expected"]
    plan = result.get("plan") or {}
    history = result.get("validation_attempts")
    if history is None:
        history = (
            [{"failed_rules": [c["rule"] for c in plan.get("checks", []) if c["status"] == "fail"]}]
            if plan
            else []
        )
    failures = [check["rule"] for check in plan.get("checks", []) if check["status"] == "fail"]
    observed = {
        "status": result["status"],
        "issue": result.get("issue"),
        "models": counters["models"],
        "sources": counters["sources"],
        "replans": result.get("replans", 0),
        "attempts": len(history),
        "sightseeing_places": len(
            {a["place_source_id"] for a in plan.get("activities", []) if a["kind"] == "sightseeing"}
        ),
        "travel_budget": (plan.get("travel_budget") or {}).get("status"),
        "cross_conflicts": (plan.get("cross_source_summary") or {}).get("conflicts", 0),
        "failed_rules": failures,
    }
    mismatches = []
    for key, value in expected.items():
        if key == "initial_failure":
            passed = bool(history) and value in history[0].get("failed_rules", [])
        elif key == "final_failure":
            passed = value in failures
        elif key == "unknown_rule":
            passed = any(
                c["rule"] == value and c["status"] == "unknown" for c in plan.get("checks", [])
            )
        else:
            passed = observed.get(key) == value
        if not passed:
            mismatches.append(key)
    cost = result["service_cost"]
    if cost["estimated_upper"] + cost["reserved_unsettled"] > cost["cap"]:
        mismatches.append("service_cap")
    if plan.get("facts_verified") or cost["actual_billed"] is not None:
        mismatches.append("unknowns_preserved")
    return {
        "id": case["id"],
        "passed": not mismatches,
        "mismatches": mismatches,
        "observed": observed,
    }


def evaluate(output):
    raw = FIXTURE.read_bytes()
    manifest = json.loads(FIXTURE.with_suffix(".sha256.json").read_text("utf-8"))
    actual = hashlib.sha256(raw).hexdigest()
    if actual != manifest["sha256"]:
        raise ValueError("frozen_acceptance_fixture_changed")
    fixture = json.loads(raw)
    cases = []
    for case in fixture["cases"]:
        try:
            result, counters, _, elapsed = run_case(case)
            report = judge(case, result, counters)
            report["elapsed_ms"] = elapsed
        except (ValueError, TypeError, AssertionError, IndexError) as exc:
            report = {"id": case["id"], "passed": False, "error_type": type(exc).__name__}
        cases.append(report)
    report = {
        "version": fixture["version"],
        "fixture_sha256": actual,
        "kind": fixture["kind"],
        "production_release_ready": False,
        "passed": all(item["passed"] for item in cases),
        "passed_cases": sum(item["passed"] for item in cases),
        "total_cases": len(cases),
        "live_network_calls": 0,
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "version",
                    "passed",
                    "passed_cases",
                    "total_cases",
                    "live_network_calls",
                )
            }
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/qa/planning-acceptance.json"
    )
    args = parser.parse_args()
    raise SystemExit(0 if evaluate(args.output)["passed"] else 1)
