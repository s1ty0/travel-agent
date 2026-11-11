"""Reuse owned evidence for local edits; unaffected days and original durations stay fixed."""

from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from .deepseek import ProviderError
from .parsing import intake
from .planner import validate_proposal
from .planning_models import Activity, DayWindow, ProductProposal
from .product_planner import ENGINE, ProductPlanner, allowed_on_day
from .run_context import RunContext
from .scheduler import VERSION, Stop, arrange
from .source_models import PlaceHit, SourceBatch, WalkingPath


def refine_product(runner, data, base_output, edit, ledger, *, cancel=None, progress=None):
    started = monotonic()
    ctx = RunContext(
        runner.settings,
        runner.key,
        runner.prices,
        ledger,
        today=runner.today,
        model_transport=runner.model_transport,
        source_transport=runner.source_transport,
        cancel=cancel,
        progress=progress,
        start=started,
    )
    old = base_output["result"]
    original = old.get("plan") or {}
    ctx.batches = [SourceBatch.model_validate(batch) for batch in old["sources"]]
    places = {
        hit.source_id: hit
        for batch in ctx.batches
        for hit in batch.hits
        if isinstance(hit, PlaceHit)
    }
    routes = {
        (binding["origin_source_id"], binding["destination_source_id"]): WalkingPath.model_validate(
            binding["route"]
        )
        for binding in original.get("route_bindings", [])
    }
    stops, notes, issue = [], [edit.note], None
    window = data.day_window or DayWindow()
    for activity in original.get("activities", []):
        day = activity["day"]
        override = next((item for item in data.day_overrides if item.day == day), None)
        active_window = override.window if override and override.window else window
        place = places.get(activity["place_source_id"])
        if place is None or not allowed_on_day(place, data, day):
            continue
        if activity["kind"] == "meal" and activity["start_minute"] >= active_window.end_minute:
            continue
        required = any(name in activity["place_name"] for name in data.constraints.required_places)
        stops.append(
            Stop(
                activity["place_source_id"],
                day,
                activity["end_minute"] - activity["start_minute"],
                activity["start_minute"],
                activity["kind"],
                required,
            )
        )

    cap = data.effective_constraints()["max_sightseeing_places"]
    if cap is not None:
        allowed = {stop.source_id for stop in stops if stop.kind == "sightseeing" and stop.required}
        for stop in stops:
            if stop.kind == "sightseeing" and len(allowed) < cap:
                allowed.add(stop.source_id)
        trimmed = [
            stop for stop in stops if stop.kind != "sightseeing" or stop.source_id in allowed
        ]
        if len(trimmed) < len(stops):
            notes.append("按新的景点总上限减少可选景点，必去地点仍保留并重新检查。")
        stops = trimmed

    def schedule():
        return arrange(
            stops,
            window,
            routes,
            max_walk_minutes=data.constraints.max_walk_minutes,
            day_overrides=data.day_overrides,
        )

    try:
        ctx.guard()
        ProductPlanner._routes(ctx, stops, places, routes)
        activities, checks, walking = schedule()
        failed_days = {
            check["day"]
            for check in checks
            if check["rule"] == "daily_walking_limit" and check["status"] == "fail"
        }
        for day in failed_days & set(edit.affected_days):
            sights = [stop for stop in stops if stop.day == day and stop.kind == "sightseeing"]
            optional = [stop for stop in sights[1:] if not stop.required]
            if optional:
                stops = [stop for stop in stops if stop not in optional]
                notes.append(f"第{day}天减少较远可选景点以降低步行，必去活动和原停留时长保留。")
        ProductPlanner._routes(ctx, stops, places, routes)
    except ProviderError as exc:
        issue = str(exc)
    activities, checks, walking = schedule()
    unchanged = [
        Activity.model_validate(
            {key: value for key, value in activity.items() if key in Activity.model_fields}
        )
        for activity in original.get("activities", [])
        if activity["day"] not in edit.affected_days
    ]
    activities = unchanged + [
        activity for activity in activities if activity.day in edit.affected_days
    ]
    request = intake(data.trip)
    plan = None
    if activities:
        plan = validate_proposal(
            ProductProposal(activities=tuple(activities)),
            request,
            ctx.batches,
            routes,
            today=runner.today,
            constraints={**data.effective_constraints(), "day_window": window.model_dump()},
            daily_activity_limit=6,
        )
        plan["checks"].extend(checks)
        for name in data.constraints.required_places:
            present = any(name in activity["place_name"] for activity in plan["activities"])
            plan["checks"].append(
                {"rule": "required_place", "status": "pass" if present else "fail"}
            )
        plan["status"] = (
            "invalid_plan" if any(c["status"] == "fail" for c in plan["checks"]) else "draft"
        )
        plan.update(
            scheduler_version=VERSION,
            draft_method="local_edit_existing_evidence",
            draft_note="；".join(notes),
            walking_minutes_by_day=walking,
            route_bindings=[
                {
                    "origin_source_id": a,
                    "destination_source_id": b,
                    "route": path.model_dump(mode="json"),
                }
                for (a, b), path in routes.items()
            ],
        )
    else:
        issue = issue or "no_feasible_activities"
    return {
        "run_id": str(uuid4()),
        "engine": ENGINE,
        "research_mode": "local_edit",
        "parent_run_id": old["run_id"],
        "model": runner.settings.model,
        "status": "stopped" if issue else plan["status"],
        "issue": issue,
        "plan": plan,
        "sources": [batch.model_dump(mode="json") for batch in ctx.batches],
        "reused_source_run_ids": [batch["run_id"] for batch in old["sources"]],
        "events": ctx.events,
        "model_calls": 0,
        "source_calls": ctx.source_calls,
        "replans": 0,
        "validation_attempts": [],
        "service_cost": ledger.summary(),
        "actual_billed_cny": None,
        "elapsed_ms": int((monotonic() - started) * 1000),
        "created_at": datetime.now(UTC).isoformat(),
        "price_checked_on": runner.prices.checked_on.isoformat(),
        "price_valid_until": runner.prices.valid_until.isoformat(),
        "amap_cost_basis": "user_confirmed_free_quota_no_paid_fallback",
    }
