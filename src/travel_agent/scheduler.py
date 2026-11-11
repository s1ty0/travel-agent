"""Deterministic visit/meal scheduling; routes are estimates, never opening-hour proof."""

import math
from dataclasses import dataclass

from .planning_models import Activity

VERSION = "constraint-scheduler-v1"


@dataclass(frozen=True)
class Stop:
    source_id: str
    day: int
    duration: int = 90
    preferred: int = 540
    kind: str = "sightseeing"
    required: bool = False


def arrange(stops, window, routes, *, max_walk_minutes=None, day_overrides=()):
    """Never shorten visits or overlap a known route. Return infeasibility explicitly."""
    activities, checks, walking = [], [], {}
    for day in sorted({stop.day for stop in stops}):
        override = next((item for item in day_overrides if item.day == day), None)
        active_window = override.window if override and override.window else window
        walk_limit = (
            override.max_walk_minutes
            if override and override.max_walk_minutes is not None
            else max_walk_minutes
        )
        cursor, previous, walked, unknown_route = active_window.start_minute, None, 0, False
        daily = sorted((s for s in stops if s.day == day), key=lambda s: s.preferred)
        for stop in daily:
            transfer = 0
            missing_route = False
            if previous is not None and previous.source_id != stop.source_id:
                route = routes.get((previous.source_id, stop.source_id))
                if route is not None:
                    minutes = math.ceil(route.duration_seconds / 60)
                    transfer = minutes + 10
                    walked += minutes
                else:
                    missing_route = True
                # No route is not zero travel: validation emits an explicit unknown.
            start = max(cursor + transfer, stop.preferred, active_window.start_minute)
            finish = start + stop.duration
            if finish > active_window.end_minute:
                checks.append(
                    {
                        "rule": "required_activity_fit" if stop.required else "activity_not_fitted",
                        "status": "fail" if stop.required else "unknown",
                        "day": day,
                        "place_source_id": stop.source_id,
                        "required_minutes": stop.duration + transfer,
                        "available_minutes": max(0, active_window.end_minute - cursor),
                    }
                )
                # An omitted stop must not contribute to actual walking or the next leg.
                if previous is not None and previous.source_id != stop.source_id:
                    route = routes.get((previous.source_id, stop.source_id))
                    if route is not None:
                        walked -= math.ceil(route.duration_seconds / 60)
                continue
            activities.append(
                Activity(
                    day=day,
                    place_source_id=stop.source_id,
                    start_minute=start,
                    end_minute=finish,
                    kind=stop.kind,
                )
            )
            cursor, previous = finish, stop
            unknown_route = unknown_route or missing_route
        walking[day] = walked
        if walk_limit is not None:
            checks.append(
                {
                    "rule": "daily_walking_limit",
                    "day": day,
                    "status": "fail"
                    if walked > walk_limit
                    else "unknown"
                    if unknown_route
                    else "pass",
                    "actual_minutes": walked,
                    "maximum_minutes": walk_limit,
                }
            )
    return activities, checks, walking
