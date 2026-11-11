"""Read-only source contracts. Search snippets are leads, not verified travel facts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Contract, NonNegative, Positive, Text


class SearchQuery(Contract):
    query: Annotated[str, Field(min_length=1, max_length=128)]
    count: Annotated[int, Field(strict=True, ge=1, le=50)] = 5

    @model_validator(mode="after")
    def not_blank(self):
        if not self.query.strip():
            raise ValueError("查询不能为空")
        return self


class Coordinate(Contract):
    longitude: Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
    latitude: Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
    system: Literal["GCJ-02", "WGS84"] = "GCJ-02"

    def as_amap(self):
        if self.system != "GCJ-02":
            raise ValueError("amap_requires_gcj02")
        return f"{self.longitude:.6f},{self.latitude:.6f}"


class PlaceQuery(Contract):
    keywords: Text
    city: Text
    page: Annotated[int, Field(strict=True, ge=1, le=8)] = 1
    count: Annotated[int, Field(strict=True, ge=1, le=25)] = 5

    @model_validator(mode="after")
    def not_blank(self):
        if not self.keywords.strip() or not self.city.strip():
            raise ValueError("地点关键词和城市不能为空")
        return self


class NearbyQuery(Contract):
    keywords: Text
    center: Coordinate
    radius_meters: Annotated[int, Field(strict=True, ge=1, le=50000)] = 5000
    page: Annotated[int, Field(strict=True, ge=1, le=8)] = 1
    count: Annotated[int, Field(strict=True, ge=1, le=25)] = 5

    @model_validator(mode="after")
    def not_blank(self):
        if not self.keywords.strip():
            raise ValueError("地点关键词不能为空")
        return self


class WalkingQuery(Contract):
    origin: Coordinate
    destination: Coordinate


class SearchHit(Contract):
    source_id: str
    url: str
    title: str
    snippet: str
    published_at: str | None = None
    evidence_kind: Literal["search_snippet"] = "search_snippet"


class PlaceHit(Contract):
    source_id: str
    poi_id: str
    name: str
    address: str | None
    adcode: str | None
    coordinate: Coordinate | None
    evidence_kind: Literal["provider_place_record"] = "provider_place_record"


class WalkingPath(Contract):
    source_id: str
    distance_meters: NonNegative
    duration_seconds: NonNegative
    mode: Literal["walking"] = "walking"
    evidence_kind: Literal["provider_route_estimate"] = "provider_route_estimate"


class SourceBatch(Contract):
    schema_version: Literal[1] = 1
    run_id: str
    provider: Literal["deepseek_search", "amap", "openstreetmap"]
    operation: Literal["search", "places", "nearby", "walking"]
    retrieved_at: datetime
    query_sha256: str
    requested_count: Positive
    received_count: NonNegative
    status: Literal["ok", "empty", "partial"]
    hits: tuple[SearchHit | PlaceHit | WalkingPath, ...]
    issues: tuple[str, ...] = ()
    next_page: Positive | None = None
    pagination_status: Literal["not_supported", "more", "end", "unknown", "limit_reached"]
    elapsed_ms: NonNegative
    call_count: Literal[1] = 1
    # Request counts are measured; account billing cannot be inferred from them.
    billed_amount_minor: None = None
    facts_verified: Literal[False] = False

    @model_validator(mode="after")
    def valid_metadata(self):
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("获取时间需要时区")
        ids = [item.source_id for item in self.hits]
        if len(set(ids)) != len(ids):
            raise ValueError("来源ID不能重复")
        if self.status == "ok" and (not self.hits or self.issues):
            raise ValueError("成功结果需要有效记录且不能有缺口")
        if self.status == "empty" and (self.hits or self.received_count or self.issues):
            raise ValueError("空结果不等于解析失败")
        return self
