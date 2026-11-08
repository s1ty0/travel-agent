"""Single-city draft contracts. Suggested times are not opening hours or reservations."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Contract, IntakeInput, Text


class PlanningInput(Contract):
    city: Text
    trip: IntakeInput

    @model_validator(mode="after")
    def city_present(self):
        if not self.city.strip():
            raise ValueError("城市不能为空")
        return self


class PlannerLimits(Contract):
    service_cap_minor: Annotated[int, Field(strict=True, ge=1, le=200)] = 200
    max_model_calls: Annotated[int, Field(strict=True, ge=1, le=4)] = 4
    max_source_calls: Annotated[int, Field(strict=True, ge=1, le=6)] = 5
    max_elapsed_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 90
    max_output_tokens: Annotated[int, Field(strict=True, ge=64, le=1024)] = 1024


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


class Activity(Contract):
    day: Annotated[int, Field(strict=True, ge=1, le=3)]
    place_source_id: Text
    start_minute: Annotated[int, Field(strict=True, ge=0, le=1439)]
    end_minute: Annotated[int, Field(strict=True, ge=1, le=1440)]
    kind: Literal["sightseeing", "meal", "rest"] = "sightseeing"
    citations: Annotated[tuple[Quote, ...], Field(max_length=3)] = ()

    @model_validator(mode="after")
    def positive_duration(self):
        if self.end_minute <= self.start_minute:
            raise ValueError("活动结束必须晚于开始")
        return self


class Proposal(Contract):
    activities: Annotated[tuple[Activity, ...], Field(min_length=1, max_length=9)]
