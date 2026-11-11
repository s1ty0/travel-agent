"""Single-city draft contracts. Suggested times are not opening hours or reservations."""

import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .claim_review import FactClaim
from .models import Contract, IntakeInput, Text
from .page_fetch import ReviewedPageURL
from .references import ReferenceInput, digest


class PlanningConstraints(Contract):
    max_sightseeing_places: Annotated[int, Field(strict=True, ge=1, le=9)] | None = None


class DayWindow(Contract):
    start_minute: Annotated[int, Field(strict=True, ge=0, le=1439)] = 540
    end_minute: Annotated[int, Field(strict=True, ge=1, le=1440)] = 1200

    @model_validator(mode="after")
    def valid_window(self):
        if self.end_minute <= self.start_minute:
            raise ValueError("day_window_end_before_start")
        return self


class PlanningInput(Contract):
    city: Text
    trip: IntakeInput
    constraints: PlanningConstraints = PlanningConstraints()
    day_window: DayWindow | None = None
    references: Annotated[tuple[ReferenceInput, ...], Field(max_length=5)] = ()
    page_urls: Annotated[
        tuple[str, ...],
        Field(max_length=2, description="明确允许本次读取并送入模型的已审查公开网址；默认不开放"),
    ] = ()

    @model_validator(mode="after")
    def city_present(self):
        if not self.city.strip():
            raise ValueError("城市不能为空")
        identities = [digest(item.model_dump_json()) for item in self.references]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate_reference")
        if len(set(self.page_urls)) != len(self.page_urls):
            raise ValueError("duplicate_page_url")
        for url in self.page_urls:
            ReviewedPageURL(url=url)
        return self

    def effective_constraints(self):
        explicit = self.constraints.max_sightseeing_places
        literal_single = any(
            re.fullmatch(
                r"(?:只(?:去|安排|游览)|最多(?:去|安排|游览)?)[一1][处个](?:景点|景区)[。！!]?",
                item.strip(),
            )
            for item in self.trip.preferences
        )
        values = [value for value in (explicit, 1 if literal_single else None) if value is not None]
        return {
            "max_sightseeing_places": min(values) if values else None,
            "explicit_limit": explicit,
            "literal_single_sight_preference": literal_single,
            "other_preferences": "not_fully_validated",
            **({"day_window": self.day_window.model_dump()} if self.day_window else {}),
        }


class PlannerLimits(Contract):
    allow_rule_draft: bool = False
    service_cap_minor: Annotated[int, Field(strict=True, ge=1, le=200)] = 200
    max_model_calls: Annotated[int, Field(strict=True, ge=1, le=4)] = 4
    max_source_calls: Annotated[int, Field(strict=True, ge=1, le=6)] = 5
    max_elapsed_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 90
    max_output_tokens: Annotated[int, Field(strict=True, ge=64, le=1024)] = 1024
    max_replans: Annotated[int, Field(strict=True, ge=0, le=1)] = 0


class SearchArgs(Contract):
    query: Annotated[str, Field(min_length=1, max_length=128)]
    count: Annotated[int, Field(strict=True, ge=1, le=3)] = 3


class PlaceArgs(Contract):
    keywords: Annotated[str, Field(min_length=1, max_length=60)]
    count: Annotated[int, Field(strict=True, ge=1, le=5)] = 3


class RouteArgs(Contract):
    origin_source_id: Text
    destination_source_id: Text


class Quote(Contract):
    source_id: Text
    quote: Annotated[str, Field(min_length=2, max_length=300)]
    start_offset: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def not_blank(self):
        if not self.quote.strip():
            raise ValueError("quote_empty")
        return self


class Activity(Contract):
    day: Annotated[int, Field(strict=True, ge=1, le=3)]
    place_source_id: Text
    start_minute: Annotated[int, Field(strict=True, ge=0, le=1439)]
    end_minute: Annotated[int, Field(strict=True, ge=1, le=1440)]
    kind: Literal["sightseeing", "meal", "rest"] = "sightseeing"
    citations: Annotated[tuple[Quote, ...], Field(max_length=3)] = ()
    claims: Annotated[tuple[FactClaim, ...], Field(max_length=3)] = ()

    @model_validator(mode="after")
    def positive_duration(self):
        if self.end_minute <= self.start_minute:
            raise ValueError("活动结束必须晚于开始")
        return self


class Proposal(Contract):
    activities: Annotated[tuple[Activity, ...], Field(min_length=1, max_length=9)]
