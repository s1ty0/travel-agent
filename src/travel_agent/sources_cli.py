"""One explicit source request; JSON query on UTF-8 stdin and private metadata in runtime/."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from .deepseek import DeepSeekSettings, ProviderError
from .evidence_store import save_provenance
from .source_models import NearbyQuery, PlaceQuery, SearchQuery, WalkingQuery
from .sources import amap_key_from_env, find_places, search_web, walking_route


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="只读来源查询；一次请求，不自动翻页或重试")
    parser.add_argument("operation", choices=("search", "places", "nearby", "walking"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print('{"error":"live_flag_required"}', file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read(8193)
        if len(raw) > 8192:
            raise ValueError("input_too_long")
        contract = {
            "search": SearchQuery,
            "places": PlaceQuery,
            "nearby": NearbyQuery,
            "walking": WalkingQuery,
        }[args.operation]
        query = contract.model_validate_json(raw)
        if args.operation == "search":
            batch = search_web(query, DeepSeekSettings.from_env())
        elif args.operation == "walking":
            batch = walking_route(query, amap_key_from_env())
        else:
            batch = find_places(query, amap_key_from_env())
    except ProviderError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (ValueError, ValidationError):
        print('{"error":"invalid_source_input"}', file=sys.stderr)
        return 2
    try:
        save_provenance(batch, Path("runtime/evidence.sqlite3"))
    except (OSError, sqlite3.Error):
        # Preserve the valid query result even if local recording fails.
        print('{"warning":"provenance_save_failed"}', file=sys.stderr)
        print(batch.model_dump_json(indent=2))
        return 3
    print(batch.model_dump_json(indent=2))
    return 0
