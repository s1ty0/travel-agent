"""Frozen product contracts and controlled three-strategy comparison; offline by default."""

import argparse
import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

from travel_agent.deepseek import DeepSeekSettings
from travel_agent.planning_models import PlanningInput
from travel_agent.product_planner import ProductPlanner
from travel_agent.service_budget import PriceCard, ServiceBudget
from travel_agent.sources import amap_key_from_env
from travel_agent.synthetic import SyntheticProviders

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/product_evaluation_v1.json"
MODES = ("single_agent", "parallel_tools", "multi_agent")


def load_cases(split):
    raw = FIXTURE.read_bytes()
    expected = FIXTURE.with_suffix(".sha256").read_text().strip()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("frozen_product_fixture_changed")
    return [case for case in json.loads(raw)["cases"] if case["split"] == split], expected


def score(case, result):
    plan = result.get("plan") or {}
    activities = plan.get("activities", [])
    ids = {
        hit["source_id"]
        for batch in result["sources"]
        for hit in batch["hits"]
        if hit.get("evidence_kind") == "provider_place_record"
    }
    failures = [c for c in plan.get("checks", []) if c["status"] == "fail"]
    invalid_ids = sum(activity["place_source_id"] not in ids for activity in activities)
    coverage = sorted({a["day"] for a in activities})
    passed = (
        result["status"] == case["expected_status"]
        and invalid_ids == 0
        and not plan.get("facts_verified", False)
        and not (result["status"] == "draft" and failures)
        and (
            result["status"] != "draft"
            or coverage == list(range(1, case["planning"]["trip"]["days"] + 1))
        )
    )
    spans = [
        e
        for e in result["events"]
        if e["operation"] in {"experience_agent", "food_agent"} and e["status"] == "ok"
    ]
    overlap = (
        max(0, min(e["end_ms"] for e in spans) - max(e["elapsed_ms"] for e in spans))
        if len(spans) == 2
        else 0
    )
    return {
        "case_id": case["id"],
        "scenario": case["scenario"],
        "passed": passed,
        "status": result["status"],
        "issue": result["issue"],
        "expected_status": case["expected_status"],
        "covered_days": coverage,
        "untraceable_activities": invalid_ids,
        "failed_rules": sorted({c["rule"] for c in failures}),
        "elapsed_ms": result["elapsed_ms"],
        "model_calls": result["model_calls"],
        "source_calls": result["source_calls"],
        "agent_overlap_ms": overlap,
        "has_plan_notes": bool(plan.get("notes")),
        "research_validation": plan.get("research_validation", {}),
        "research_failures": sum(
            e["status"] == "failed"
            for e in result["events"]
            if e["operation"] in {"combined_research", "experience_agent", "food_agent"}
        ),
        "diagnostics": [e for e in result["events"] if e["operation"] == "response_validation"],
        "known_model_estimate_micro": (
            sum(
                e.get("prompt_tokens", 0) * result.get("model_rates_micro", {"input": 2})["input"]
                + e.get("completion_tokens", 0)
                * result.get("model_rates_micro", {"output": 8})["output"]
                for e in result["events"]
                if e["operation"] in {"combined_research", "experience_agent", "food_agent"}
            )
            if result["service_cost"]["status"] != "uncertain"
            else None
        ),
        "service_cost": result["service_cost"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Frozen product evaluation; --live uses real models, synthetic sources"
    )
    parser.add_argument("--split", choices=("development", "acceptance"), default="development")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-sources", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/evaluation/product-offline.json"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output_exists_no_overwrite_or_repeat")
    modes = args.modes.split(",")
    if any(mode not in MODES for mode in modes) or not 1 <= args.limit <= 30:
        parser.error("invalid_modes_or_limit")
    if args.live and args.limit > 10:
        parser.error("live_batch_requires_explicit_limit_at_most_10")
    if args.live_sources and (not args.live or args.limit > 3):
        parser.error("live_sources_requires_live_and_limit_at_most_3")
    settings = DeepSeekSettings.from_env() if args.live else DeepSeekSettings("synthetic-model")
    today = date.today() if args.live else date(2026, 11, 11)
    prices = PriceCard(
        amap_free_quota_only=(os.environ.get("AMAP_FREE_QUOTA_ONLY") == "1")
        if args.live_sources
        else True
    )
    if args.live_sources and not prices.amap_free_quota_only:
        parser.error("live_sources_requires_confirmed_free_quota")
    prices.validate(settings.model, today)
    key = amap_key_from_env() if args.live_sources else "synthetic-amap"
    cases, digest = load_cases(args.split)
    selected = cases[: args.limit]
    report = {
        "version": "product-evaluation-v1",
        "fixture_sha256": digest,
        "created_at": datetime.now(UTC).isoformat(),
        "split": args.split,
        "model_execution": "live" if args.live else "simulated",
        "source_execution": "live" if args.live_sources else "synthetic_replay",
        "attempts_per_case_mode": 1,
        "cap_minor_per_run": 500,
        "comparison_limits": (
            "Combined output ceiling2048 vs specialists1536+512; input duplicated for specialists. "
            "Synthetic source delay20ms; not real end-to-end latency or blind evaluation."
        ),
        "code_sha256": hashlib.sha256(
            b"".join(path.read_bytes() for path in sorted((ROOT / "src/travel_agent").glob("*.py")))
        ).hexdigest(),
        "results": [],
        "summaries": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in selected:
        for mode in modes:
            synthetic = SyntheticProviders(flags=case.get("source_flags"), latency=0.02)
            runner = ProductPlanner(
                settings,
                key,
                prices,
                mode=mode,
                today=today,
                model_transport=None if args.live else synthetic.model_transport,
                source_transport=None if args.live_sources else synthetic.source_transport,
            )
            result = runner.run(
                PlanningInput.model_validate(case["planning"]), ledger=ServiceBudget(500)
            )
            item = {"mode": mode, **score(case, result)}
            item["actual_model_calls"] = result["model_calls"] if args.live else 0
            item["actual_source_calls"] = result["source_calls"] if args.live_sources else 0
            report["results"].append(item)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps({key: item[key] for key in ("case_id", "mode", "status", "passed")}),
                flush=True,
            )
    for mode in modes:
        rows = [row for row in report["results"] if row["mode"] == mode]
        report["summaries"][mode] = {
            "cases": len(rows),
            "correct_outcomes": sum(row["passed"] for row in rows),
            "drafts": sum(row["status"] == "draft" for row in rows),
            "expected_refusals": sum(row["passed"] and row["status"] != "draft" for row in rows),
            "median_elapsed_ms": median(row["elapsed_ms"] for row in rows),
            "overlapping_agent_runs": sum(row["agent_overlap_ms"] > 0 for row in rows),
            "model_estimate_micro": sum(row["known_model_estimate_micro"] or 0 for row in rows),
            "unknown_model_cost_runs": sum(
                row["known_model_estimate_micro"] is None for row in rows
            ),
            "invalid_research_place_ids": sum(
                row["research_validation"].get("invalid_place_ids", 0) for row in rows
            ),
            "program_filled_days": sum(
                row["research_validation"].get("program_filled_days", 0) for row in rows
            ),
            "research_failures": sum(row["research_failures"] for row in rows),
        }
    report["passed"] = all(row["passed"] for row in report["results"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "summaries": report["summaries"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
