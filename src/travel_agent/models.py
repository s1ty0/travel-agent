"""Validated input contracts. Monetary values are integer CNY cents."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]
NonNegative = Annotated[int, Field(strict=True, ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
Scope = Literal["total", "per_person", "per_day", "per_person_per_day"]
Category = Literal["transport", "lodging", "food", "tickets", "local_transport", "fees"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetSpec(Contract):
    raw_text: str
    currency: Literal["CNY"] = "CNY"
    kind: Literal["ceiling", "target", "range"]
    target_minor: Positive | None = None
    minimum_minor: Positive | None = None
    maximum_minor: Positive | None = None
    scope: Scope | None = None
    cap_confirmed: bool = False
    strict_minimum: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.kind == "target":
            if self.target_minor is None or self.maximum_minor or self.minimum_minor:
                raise ValueError("目标预算只能包含目标金额")
            if self.cap_confirmed or self.strict_minimum:
                raise ValueError("目标金额不能冒充已确认的上限或下限")
        elif self.kind == "ceiling":
            if self.maximum_minor is None or self.target_minor or self.minimum_minor:
                raise ValueError("上限预算只能包含最高金额")
            if self.strict_minimum:
                raise ValueError("上限预算没有严格下限")
        else:
            if self.minimum_minor is None or self.maximum_minor is None or self.target_minor:
                raise ValueError("范围预算必须包含上下界")
            if self.minimum_minor > self.maximum_minor:
                raise ValueError("预算下限不能超过上限")
        return self


class DestinationSpec(Contract):
    raw_text: str
    mode: Literal["exact", "nearby"]
    name: Text
    radius_km: Positive | None = None
    max_one_way_minutes: Positive | None = None
    entity_status: Literal["unresolved"] = "unresolved"


class IntakeInput(Contract):
    raw_text: Annotated[str, Field(max_length=4000)] = ""
    destination: Text | None = None
    budget: Text | None = None
    origin: Text | None = None
    adults: Positive | None = None
    children: NonNegative = 0
    start_date: date | None = None
    end_date: date | None = None
    days: Positive | None = None
    transport_mode: Literal["rail", "flight", "car", "public", "mixed"] | None = None
    radius_km: Positive | None = None
    max_one_way_minutes: Positive | None = None
    budget_scope: Scope | None = None
    cap_confirmed: bool = False
    included_categories: tuple[Category, ...] | None = None
    preferences: tuple[Text, ...] = ()
    accessibility_needs: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def dates_valid(self):
        if self.end_date and not self.start_date:
            raise ValueError("结束日期需要同时提供开始日期")
        if self.start_date and self.end_date:
            duration = (self.end_date - self.start_date).days + 1
            if duration <= 0:
                raise ValueError("结束日期不能早于开始日期")
            if self.days is not None and self.days != duration:
                raise ValueError("天数与起止日期不一致")
        for value in (self.origin, self.destination, self.budget):
            if value is not None and not value.strip():
                raise ValueError("地点或预算不能只有空白")
        if self.included_categories is not None and (
            not self.included_categories
            or len(set(self.included_categories)) != len(self.included_categories)
        ):
            raise ValueError("费用包含项不能为空或重复")
        return self


class TripRequest(Contract):
    input: IntakeInput
    destination: DestinationSpec | None
    budget: BudgetSpec | None
    days: Positive | None
    missing_fields: tuple[str, ...]
    assumptions: tuple[str, ...]
    status: Literal["draft", "input_complete"]


class SourceRecord(Contract):
    source_id: Text
    locator: Text
    retrieved_at: datetime
    content_kind: Literal["original", "search_snippet", "platform_summary", "user_import"]

    @model_validator(mode="after")
    def timezone_required(self):
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("来源获取时间需要时区")
        return self


class EvidenceClaim(Contract):
    claim: Text
    source_ids: tuple[Text, ...]
    status: Literal["verified", "estimated", "unverified", "conflicting"] = "unverified"

    @model_validator(mode="after")
    def verified_needs_source(self):
        if self.status == "verified" and not self.source_ids:
            raise ValueError("已核实主张必须有来源")
        return self


class CostItem(Contract):
    item_id: Text
    category: Category
    low_minor: NonNegative | None = None
    high_minor: NonNegative | None = None
    unit: Literal["total", "person", "day", "person_day", "room_night"] = "total"
    quantity: Positive = 1
    taxes_included: bool = True
    estimated: bool = True

    @model_validator(mode="after")
    def bounds_valid(self):
        if (self.low_minor is None) != (self.high_minor is None):
            raise ValueError("已知费用必须同时提供上下界")
        if self.low_minor is not None and self.low_minor > self.high_minor:
            raise ValueError("费用下限不能超过上限")
        return self


class LedgerInput(Contract):
    budget: BudgetSpec
    items: tuple[CostItem, ...]
    required_categories: tuple[Category, ...]
    travelers: Positive | None = None
    days: Positive | None = None
    rooms: Positive | None = None
    nights: NonNegative | None = None
    reserve_minor: NonNegative = 0

    @model_validator(mode="after")
    def unique_items(self):
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("费用条目 ID 不可重复，避免重复计费")
        if not self.required_categories or len(set(self.required_categories)) != len(
            self.required_categories
        ):
            raise ValueError("必须明确不重复的费用包含项")
        return self


class ValidationReport(Contract):
    status: Literal["within_estimated_cap", "over_cap", "uncertain", "below_strict_minimum"]
    low_minor: NonNegative
    high_minor: NonNegative | None
    cap_minor: Positive | None
    unknowns: tuple[str, ...]
    note: str = "只检查声明范围内的估算费用，不代表实时价格或可预订性。"
