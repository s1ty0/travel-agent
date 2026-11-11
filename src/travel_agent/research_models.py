"""Bounded agent outputs: select sourced entities, never invent itinerary facts."""

from typing import Annotated, Literal

from pydantic import Field

from .models import Contract, Text


class VisitChoice(Contract):
    day: Annotated[int, Field(strict=True, ge=1, le=3)]
    place_source_id: Text
    duration_minutes: Annotated[int, Field(strict=True, ge=30, le=180)] = 90


class ExperienceResearch(Contract):
    visits: Annotated[tuple[VisitChoice, ...], Field(min_length=1, max_length=6)]


class FoodResearch(Contract):
    keyword: Literal["餐厅", "本地菜", "小吃", "面馆", "素食"] = "餐厅"


class CombinedResearch(Contract):
    experience: ExperienceResearch
    food: FoodResearch
