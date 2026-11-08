"""Explicit single-page read; only minimal metadata is automatically persisted."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .deepseek import DeepSeekSettings, ProviderError
from .page_fetch import PageReadInput, read_page
from .service_budget import ServiceBudget


def save_page_audit(outcome, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # report() excludes supplied quotes, provider title, Markdown, and credentials.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS page_reads "
            "(run_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO page_reads VALUES (?, ?)",
            (outcome.run_id, json.dumps(outcome.report(), ensure_ascii=False)),
        )


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="单页受控读取；非事实认证，不自动保存正文")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--include-content", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print('{"error":"live_flag_required"}', file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read(8193)
        if len(raw) > 8192:
            raise ValueError("input_too_long")
        query = PageReadInput.model_validate_json(raw)
        ledger = ServiceBudget(int(os.environ.get("SERVICE_BUDGET_MINOR", "200")))
        outcome = read_page(query, DeepSeekSettings.from_env(), ledger=ledger)
    except ProviderError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (TypeError, ValueError):
        print('{"error":"invalid_page_input"}', file=sys.stderr)
        return 2
    result = outcome.report(include_content=args.include_content)
    try:
        save_page_audit(outcome, Path("runtime/evidence.sqlite3"))
    except (OSError, sqlite3.Error):
        print('{"warning":"page_audit_save_failed"}', file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if outcome.status == "acquired" else 2
