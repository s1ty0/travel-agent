"""Source-first product planner with specialist research and deterministic scheduling."""

import math
from datetime import UTC, date, datetime
from time import monotonic
from uuid import uuid4

from .deepseek import ProviderError
from .parsing import intake
from .planner import validate_proposal
from .planning_models import DayWindow, ProductProposal
from .research_models import CombinedResearch, ExperienceResearch, FoodResearch, VisitChoice
from .run_context import RunContext
from .scheduler import VERSION, Stop, arrange
from .source_models import NearbyQuery, PlaceHit, PlaceQuery, SearchQuery, WalkingPath, WalkingQuery

ENGINE = "source-first-v1.1"


def distance(first, second):
    """Geographical ranking only; never used as a travel-time estimate."""
    if first.coordinate is None or second.coordinate is None:
        return float("inf")
    a, b = first.coordinate, second.coordinate
    x = math.radians(a.longitude - b.longitude) * math.cos(math.radians(a.latitude))
    y = math.radians(a.latitude - b.latitude)
    return math.hypot(x, y)


def usable_place(place, excluded=()):
    return isinstance(place, PlaceHit) and not any(
        word in place.name for word in ("停车", "入口", "出口", "厕所", "售票", *excluded)
    )


def allowed_on_day(place, data, day):
    override = next((item for item in data.day_overrides if item.day == day), None)
    names = (*data.constraints.excluded_places, *(override.excluded_places if override else ()))
    ids = (
        *data.constraints.excluded_source_ids,
        *(override.excluded_source_ids if override else ()),
    )
    return place.source_id not in ids and usable_place(place, names)


class ProductPlanner:
    def __init__(
        self,
        settings,
        key,
        prices,
        *,
        mode="single_agent",
        today=None,
        model_transport=None,
        source_transport=None,
    ):
        if mode not in {"single_agent", "parallel_tools", "multi_agent"}:
            raise ValueError("invalid_research_mode")
        self.settings, self.key, self.prices, self.mode = settings, key, prices, mode
        self.today = today or date.today()
        self.model_transport, self.source_transport = model_transport, source_transport

    def run(
        self,
        data,
        *,
        ledger,
        initial_model_calls=0,
        initial_events=(),
        started_at=None,
        cancel=None,
        progress=None,
    ):
        started = started_at if started_at is not None else monotonic()
        ctx = RunContext(
            self.settings,
            self.key,
            self.prices,
            ledger,
            today=self.today,
            model_transport=self.model_transport,
            source_transport=self.source_transport,
            cancel=cancel,
            progress=progress,
            start=started,
        )
        ctx.model_calls = initial_model_calls
        ctx.events.extend(initial_events)
        plan, issue, routes, stops, notes = None, None, {}, [], []
        request = intake(data.trip)
        window = data.day_window or DayWindow()
        constraints = data.effective_constraints()
        places = {}
        research_validation = {
            "proposed_visits": 0,
            "invalid_place_ids": 0,
            "rejected_assignments": 0,
            "program_filled_days": 0,
        }
        try:
            if (
                request.days not in (1, 2, 3)
                or not request.destination
                or request.destination.mode != "exact"
            ):
                raise ProviderError("select_exact_destination_and_1_to_3_days")
            if request.destination.name != data.city:
                raise ProviderError("destination_city_mismatch")
            if data.trip.transport_mode not in (None, "mixed", "public"):
                raise ProviderError("baseline_walking_mode_not_confirmed")
            interests = data.trip.raw_text + " " + " ".join(data.trip.preferences)
            keyword = next(
                (
                    word
                    for word in ("博物馆", "公园", "古镇", "园林", "寺庙")
                    if word in interests and ("不要" + word) not in interests
                ),
                "风景名胜",
            )
            food_keyword = "素食" if "素食" in interests or "吃素" in interests else "餐厅"
            queries = [
                SearchQuery(query=f"{data.city} 旅行 景点 用餐 预约 注意事项", count=3),
                PlaceQuery(city=data.city, keywords=keyword, count=15),
                PlaceQuery(city=data.city, keywords=food_keyword, count=10),
            ]
            if self.mode == "single_agent":
                batches = [ctx.source(query) for query in queries]
            else:
                batches = ctx.parallel([lambda query=query: ctx.source(query) for query in queries])
                for result in batches:
                    if isinstance(result, Exception):
                        raise result
            food_pool = {
                hit.source_id: hit
                for hit in batches[2].hits
                if usable_place(hit, data.constraints.excluded_places)
            }
            for batch in batches[:2]:
                for hit in batch.hits:
                    if usable_place(hit, data.constraints.excluded_places):
                        places[hit.source_id] = hit
            for name in data.constraints.required_places:
                batch = ctx.source(PlaceQuery(city=data.city, keywords=name, count=3))
                for hit in batch.hits:
                    if usable_place(hit, data.constraints.excluded_places):
                        places[hit.source_id] = hit
            if not places:
                raise ProviderError("insufficient_sources")
            research_data = {
                "city": data.city,
                "days": request.days,
                "trip": data.trip.model_dump(mode="json"),
                "constraints": data.constraints.model_dump(mode="json"),
                "places": [hit.model_dump(mode="json") for hit in places.values()],
                "search_leads": [hit.model_dump(mode="json") for hit in batches[0].hits],
                "food_places": [hit.model_dump(mode="json") for hit in food_pool.values()],
            }
            food = FoodResearch()
            experience = None
            if self.mode in {"single_agent", "parallel_tools"}:
                try:
                    combined = ctx.model("combined_research", CombinedResearch, research_data)
                    experience, food = combined.experience, combined.food
                except Exception:
                    ctx.guard()  # Unknown cost, cancellation or deadline cannot use fallback.
                    notes.append(
                        "模型研究未形成有效候选，按已查询地点提供保守初稿，偏好适配待确认。"
                    )
            else:
                branches = ctx.parallel(
                    [
                        lambda: ctx.model(
                            "experience_agent",
                            ExperienceResearch,
                            research_data,
                            output_tokens=1536,
                        ),
                        lambda: ctx.model(
                            "food_agent", FoodResearch, research_data, output_tokens=512
                        ),
                    ]
                )
                ctx.guard()
                if isinstance(branches[0], ExperienceResearch):
                    experience = branches[0]
                else:
                    notes.append("体验研究分支失败，保留已查询地点起草，偏好适配待确认。")
                if isinstance(branches[1], FoodResearch):
                    food = branches[1]
                else:
                    notes.append("餐饮研究分支失败，使用中性的餐厅查询词，口味偏好待确认。")
            choices = self._choices(
                experience, places, data, request.days, notes, research_validation
            )
            preferred_food = set(food.recommended_source_ids) & food_pool.keys()
            if len(preferred_food) < len(set(food.recommended_source_ids)):
                notes.append("餐饮研究中未取得来源的推荐ID已剔除，餐厅由实际附近结果提供。")
            for day in range(1, request.days + 1):
                visits = [choice for choice in choices if choice.day == day]
                if not visits:
                    continue
                first = places[visits[0].place_source_id]
                for index, choice in enumerate(visits):
                    required = any(
                        name in places[choice.place_source_id].name
                        for name in data.constraints.required_places
                    )
                    stops.append(
                        Stop(
                            choice.place_source_id,
                            day,
                            choice.duration_minutes,
                            window.start_minute if index == 0 else 840,
                            required=required,
                        )
                    )
                if first.coordinate is None:
                    notes.append(f"第{day}天的地点缺少坐标，附近餐厅与步行时间待查。")
                    continue
                meal_batch = ctx.source(
                    NearbyQuery(
                        keywords=food.keyword, center=first.coordinate, radius_meters=1500, count=5
                    )
                )
                meals = [hit for hit in meal_batch.hits if allowed_on_day(hit, data, day)]
                meals.sort(
                    key=lambda hit: (hit.source_id not in preferred_food, distance(first, hit))
                )
                for hit in meals:
                    places[hit.source_id] = hit
                for index, (low, high, preferred) in enumerate(
                    ((690, 840, 720), (1050, 1200, 1080))
                ):
                    if meals and min(window.end_minute, high) - max(window.start_minute, low) >= 60:
                        chosen = meals[min(index, len(meals) - 1)]
                        stops.append(Stop(chosen.source_id, day, 60, preferred, "meal"))
            self._routes(ctx, stops, places, routes)
            activities, extra_checks, walking = arrange(
                stops,
                window,
                routes,
                max_walk_minutes=data.constraints.max_walk_minutes,
                day_overrides=data.day_overrides,
            )
            if data.constraints.max_walk_minutes is not None or data.day_overrides:
                overloaded = {
                    check["day"]
                    for check in extra_checks
                    if check["rule"] == "daily_walking_limit" and check["status"] == "fail"
                }
                removable = set()
                for day in overloaded:
                    sights = [s for s in stops if s.day == day and s.kind == "sightseeing"]
                    if len(sights) > 1:
                        removable.update((day, s.source_id) for s in sights[1:] if not s.required)
                if removable:
                    stops = [s for s in stops if (s.day, s.source_id) not in removable]
                    notes.append("为遵守步行上限，减少了较远的可选景点；必去地点未删除。")
                    self._routes(ctx, stops, places, routes)
                    activities, extra_checks, walking = arrange(
                        stops,
                        window,
                        routes,
                        max_walk_minutes=data.constraints.max_walk_minutes,
                        day_overrides=data.day_overrides,
                    )
            # Removing an optional stop can expose another pair; only its own route is valid.
            final_stops = [
                Stop(
                    a.place_source_id, a.day, a.end_minute - a.start_minute, a.start_minute, a.kind
                )
                for a in activities
            ]
            before = len(routes)
            self._routes(ctx, final_stops, places, routes)
            if len(routes) != before:
                activities, extra_checks, walking = arrange(
                    stops,
                    window,
                    routes,
                    max_walk_minutes=data.constraints.max_walk_minutes,
                    day_overrides=data.day_overrides,
                )
            if not activities:
                raise ProviderError("no_feasible_activities")
            plan = validate_proposal(
                ProductProposal(activities=tuple(activities)),
                request,
                ctx.batches,
                routes,
                today=self.today,
                constraints={**constraints, "day_window": window.model_dump()},
                daily_activity_limit=6,
            )
            plan["checks"].extend(extra_checks)
            for required in data.constraints.required_places:
                present = any(required in activity["place_name"] for activity in plan["activities"])
                plan["checks"].append(
                    {"rule": "required_place", "status": "pass" if present else "fail"}
                )
            plan["status"] = (
                "invalid_plan" if any(c["status"] == "fail" for c in plan["checks"]) else "draft"
            )
            plan.update(
                research_validation=research_validation,
                scheduler_version=VERSION,
                draft_method="model_research_program_schedule",
                walking_minutes_by_day=walking,
                notes=notes,
                route_bindings=[
                    {
                        "origin_source_id": a,
                        "destination_source_id": b,
                        "route": route.model_dump(mode="json"),
                    }
                    for (a, b), route in routes.items()
                ],
            )
            if notes:
                plan["draft_note"] = "；".join(notes)
        except ProviderError as exc:
            issue = str(exc)
        except Exception as exc:
            category = type(exc).__name__
            category = (
                category
                if category in {"ValueError", "TypeError", "KeyError", "ValidationError"}
                else "response_error"
            )
            frame, location = exc.__traceback__, "run"
            while frame:
                name = frame.tb_frame.f_code.co_name
                if name in {"source", "arrange", "_choices", "_routes", "validate_proposal"}:
                    location = name
                frame = frame.tb_next
            ctx.event("response_validation", "failed", error_category=category, location=location)
            issue = "invalid_model_or_tool_response"
        if plan is None and stops:
            # A supplier failure stops spending, not access to already acquired evidence.
            retained, checks, walking = arrange(
                stops,
                window,
                routes,
                max_walk_minutes=data.constraints.max_walk_minutes,
                day_overrides=data.day_overrides,
            )
            if retained:
                plan = validate_proposal(
                    ProductProposal(activities=tuple(retained)),
                    request,
                    ctx.batches,
                    routes,
                    today=self.today,
                    constraints={**constraints, "day_window": window.model_dump()},
                    daily_activity_limit=6,
                )
                plan["checks"].extend(checks)
                plan["draft_note"] = "来源调用已停止，保留已取得资料；缺失交通和后续安排待核实。"
                plan["scheduler_version"] = VERSION
                plan["route_bindings"] = [
                    {
                        "origin_source_id": a,
                        "destination_source_id": b,
                        "route": route.model_dump(mode="json"),
                    }
                    for (a, b), route in routes.items()
                ]
        return {
            "run_id": str(uuid4()),
            "engine": ENGINE,
            "research_mode": self.mode,
            "model": self.settings.model,
            "status": "stopped" if issue else plan["status"],
            "issue": issue,
            "plan": plan,
            "events": ctx.events,
            "sources": [batch.model_dump(mode="json") for batch in ctx.batches],
            "page_reads": [],
            "reference_documents": [],
            "service_cost": ledger.summary(),
            "model_calls": ctx.model_calls,
            "source_calls": ctx.source_calls,
            "replans": 0,
            "validation_attempts": [],
            "elapsed_ms": int((monotonic() - started) * 1000),
            "actual_billed_cny": None,
            "created_at": datetime.now(UTC).isoformat(),
            "price_checked_on": self.prices.checked_on.isoformat(),
            "price_valid_until": self.prices.valid_until.isoformat(),
            "model_rates_micro": {
                "input": self.prices.input_micro_cny_per_token,
                "output": self.prices.output_micro_cny_per_token,
            },
            "amap_cost_basis": "user_confirmed_free_quota_no_paid_fallback",
        }

    @staticmethod
    def _choices(experience, places, data, days, notes, validation=None):
        validation = validation if validation is not None else {}
        validation["proposed_visits"] = len(experience.visits) if experience else 0
        limit = data.effective_constraints()["max_sightseeing_places"] or 2 * days
        choices = []
        if experience:
            seen = set()
            for choice in experience.visits:
                if (
                    choice.place_source_id not in places
                    or choice.day > days
                    or not allowed_on_day(places[choice.place_source_id], data, choice.day)
                    or choice.place_source_id in seen
                    or sum(c.day == choice.day for c in choices) >= 2
                ):
                    key = (
                        "invalid_place_ids"
                        if choice.place_source_id not in places
                        else "rejected_assignments"
                    )
                    validation[key] = validation.get(key, 0) + 1
                    notes.append("研究候选含无效、重复或超范围地点，已排除；不生成虚构引用。")
                    continue
                if len(choices) < limit:
                    choices.append(choice)
                    seen.add(choice.place_source_id)
        selected = {choice.place_source_id for choice in choices}
        remaining = [place for key, place in places.items() if key not in selected]
        for day in range(1, days + 1):
            eligible = [place for place in remaining if allowed_on_day(place, data, day)]
            if (
                not any(choice.day == day for choice in choices)
                and eligible
                and len(choices) < limit
            ):
                place = eligible[0]
                remaining.remove(place)
                choices.append(VisitChoice(day=day, place_source_id=place.source_id))
                validation["program_filled_days"] = validation.get("program_filled_days", 0) + 1
                notes.append(f"第{day}天按已查询地点补充轻量初稿，体验适配待确认。")
        return choices

    @staticmethod
    def _routes(ctx, stops, places, routes):
        ordered = sorted(stops, key=lambda stop: (stop.day, stop.preferred))
        for first, second in zip(ordered, ordered[1:], strict=False):
            pair = first.source_id, second.source_id
            if first.day != second.day or pair[0] == pair[1] or pair in routes:
                continue
            a, b = places[pair[0]], places[pair[1]]
            if a.coordinate is None or b.coordinate is None:
                continue
            batch = ctx.source(WalkingQuery(origin=a.coordinate, destination=b.coordinate))
            paths = [hit for hit in batch.hits if isinstance(hit, WalkingPath)]
            if paths:
                routes[pair] = min(paths, key=lambda path: path.duration_seconds)
