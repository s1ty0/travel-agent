"""Offline reference import/check/save/replay/delete. Never reads keys or calls a provider."""

import argparse
import json
import sys
from pathlib import Path

from .references import (
    EvidenceInspection,
    QuoteInspection,
    delete_snapshot,
    import_reference,
    inspection_report,
    load_snapshot,
    save_snapshot,
)


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="离线资料与引文定位；不自动抓取链接")
    parser.add_argument("operation", choices=("inspect", "save", "replay", "delete"))
    parser.add_argument("--snapshot")
    args = parser.parse_args()
    root = Path("runtime/reference_snapshots")
    try:
        if args.operation in ("replay", "delete") and args.snapshot is None:
            raise ValueError("snapshot_id_required")
        if args.operation in ("inspect", "save") and args.snapshot is not None:
            raise ValueError("snapshot_id_not_applicable")
        if args.operation == "delete":
            delete_snapshot(args.snapshot, root)
            print('{"status":"deleted","network_calls":0}')
            return 0
        raw = sys.stdin.read(64001)
        if len(raw) > 64000:
            raise ValueError("evidence_input_too_large")
        if args.operation == "replay":
            documents = load_snapshot(args.snapshot, root)
            queries = QuoteInspection.model_validate_json(raw or "{}").quotes
        else:
            data = EvidenceInspection.model_validate_json(raw)
            documents = tuple(import_reference(item) for item in data.references)
            if len({d.source_id for d in documents}) != len(documents):
                raise ValueError("duplicate_reference")
            queries = data.quotes
        report = inspection_report(documents, queries)
        if args.operation == "save":
            report["snapshot_id"] = save_snapshot(documents, root)
        if args.operation == "replay":
            report["snapshot_id"] = args.snapshot
            report["mode"] = "offline_replay"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError):
        print('{"error":"invalid_or_unavailable_reference","network_calls":0}', file=sys.stderr)
        return 2
