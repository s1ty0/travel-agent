"""Server-owned repair scope: shift time windows or remove existing activities only."""

import json
from collections import Counter

from .references import digest


def _signature(activity):
    fields = activity.model_dump(mode="json", exclude={"start_minute", "end_minute"})
    fields["duration_minutes"] = activity.end_minute - activity.start_minute
    return digest(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def repair_mode(failed_rules):
    rules = set(failed_rules)
    if rules <= {"time_overlap", "transfer_duration"}:
        return "shift_times_only"
    if rules <= {
        "time_overlap",
        "transfer_duration",
        "sightseeing_place_limit",
        "daily_activity_limit",
    }:
        return "remove_or_shift_only"
    return "rebuild_for_day_coverage"


def scope_feedback(proposal, mode):
    return {
        "mode": mode,
        "preserve_non_time_fields_and_duration": mode != "rebuild_for_day_coverage",
        "allow_removal": mode == "remove_or_shift_only",
        "activities": [
            {
                "day": item.day,
                "place_source_id": item.place_source_id,
                "kind": item.kind,
                "duration_minutes": item.end_minute - item.start_minute,
                "non_time_signature_sha256": _signature(item),
            }
            for item in proposal.activities
        ],
    }


def scope_matches(original, revised, mode):
    before = Counter(_signature(item) for item in original.activities)
    after = Counter(_signature(item) for item in revised.activities)
    if mode == "shift_times_only":
        return before == after
    if mode == "remove_or_shift_only":
        return all(count <= before[key] for key, count in after.items())
    return mode == "rebuild_for_day_coverage"
