"""Explicit paid baseline CLI; private minimal audit only, no transactions or auto upgrades."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .deepseek import DeepSeekSettings, ProviderError
from .evidence_store import save_provenance
from .plan_render import render_markdown
from .planner import BaselineRunner
from .planning_models import PlannerLimits, PlanningInput
from .service_budget import PriceCard
from .source_models import SourceBatch
from .sources import amap_key_from_env


def save_run(result, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not persist user input, prompt, private reasoning, itinerary, or evidence snippets.
    metadata = {
        key: result.get(key)
        for key in (
            "status",
            "issue",
            "events",
            "service_cost",
            "model_calls",
            "source_calls",
            "elapsed_ms",
            "model",
            "price_checked_on",
            "price_valid_until",
            "amap_cost_basis",
            "page_reads",
        )
    }
    metadata["claim_review_summary"] = (result.get("plan") or {}).get("claim_review_summary")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS planning_runs "
            "(run_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO planning_runs VALUES (?, ?)",
            (result["run_id"], json.dumps(metadata, ensure_ascii=False)),
        )


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="受控单Agent草案；每次服务预算最高2元，不执行购买")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    if not args.live:
        print('{"error":"live_flag_required"}', file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read(64001)
        if len(raw) > 64000:
            raise ValueError("input_too_long")
        data = PlanningInput.model_validate_json(raw)
        limits = PlannerLimits(service_cap_minor=int(os.environ.get("SERVICE_BUDGET_MINOR", "200")))
        result = BaselineRunner(
            DeepSeekSettings.from_env(),
            amap_key_from_env(),
            limits=limits,
            prices=PriceCard(amap_free_quota_only=os.environ.get("AMAP_FREE_QUOTA_ONLY") == "1"),
        ).run(data)
    except ProviderError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (TypeError, ValueError):
        print('{"error":"invalid_planning_input"}', file=sys.stderr)
        return 2
    try:
        path = Path("runtime/evidence.sqlite3")
        for batch in result["sources"]:
            save_provenance(SourceBatch.model_validate(batch), path)
        save_run(result, path)
    except (OSError, sqlite3.Error):
        print('{"warning":"planning_audit_save_failed"}', file=sys.stderr)
        print(
            render_markdown(result)
            if args.format == "markdown"
            else json.dumps(result, ensure_ascii=False, indent=2)
        )
        return 3
    print(
        render_markdown(result)
        if args.format == "markdown"
        else json.dumps(result, ensure_ascii=False, indent=2)
    )
    return 0 if result["status"] == "draft" else 2
