"""Explicit offline demonstration. Synthetic transports never fall through to network."""

import os
import re
from datetime import date

from .deepseek import DeepSeekSettings, ProviderError
from .models import IntakeInput
from .planning_models import PlanningInput
from .product_planner import ProductPlanner
from .service_budget import PriceCard
from .synthetic import CENTERS, SyntheticProviders


def demo_enabled():
    return os.environ.get("TRAVEL_DEMO_MODE") == "1"


def demo_runner(mode):
    synthetic = SyntheticProviders()
    prices = PriceCard(amap_free_quota_only=True)
    settings = DeepSeekSettings("synthetic-demo-only")
    return ProductPlanner(
        settings,
        "synthetic-demo-only",
        prices,
        mode=mode,
        today=date(2026, 11, 11),
        model_transport=synthetic.model_transport,
        source_transport=synthetic.source_transport,
    )


def demo_intake(text, base=None):
    cities = [city for city in CENTERS if city in text]
    if len(cities) != 1 or "附近" in text or "周边" in text:
        raise ProviderError("demo_select_nanjing_hangzhou_or_shanghai")
    mentions = re.findall(r"([\d一二两三四五六七八九十]+)\s*天", text)
    if len(set(mentions)) > 1 or any(
        value not in {"1", "2", "3", "一", "二", "两", "三"} for value in mentions
    ):
        raise ProviderError("demo_select_nanjing_hangzhou_or_shanghai")
    found = re.search(r"([123一二两三])\s*天", text)
    days = (
        int(found[1])
        if found and found[1].isdigit()
        else {"一": 1, "二": 2, "两": 2, "三": 3}.get(found[1], 1)
        if found
        else 1
    )
    amount = re.search(r"预算\s*(\d+\s*(?:[~～至\-]\s*\d+\s*)?(?:元)?(?:以内|以下)?)", text)
    if not amount:
        amount = re.search(r"(\d+\s*(?:[~～至\-]\s*\d+\s*)?元(?:以内|以下)?)", text)
    budget = amount[1] if amount else None
    if base:
        trip = base.trip.model_copy(
            update={"destination": cities[0], "days": days, "raw_text": text}
        )
        if budget:
            trip = trip.model_copy(update={"budget": budget})
        data = base.model_copy(update={"city": cities[0], "trip": trip, "day_overrides": ()})
        data = PlanningInput.model_validate(data.model_dump())
    else:
        data = PlanningInput(
            city=cities[0],
            trip=IntakeInput(destination=cities[0], days=days, budget=budget, raw_text=text),
        )
    return data, [
        "离线合成演示：仅支持南京、杭州、上海1～3天；未写天数暂按1天，未调用真实模型或地图。"
    ]


def mark_demo(output):
    result = output["result"]
    output["is_demo"] = result["is_demo"] = True
    result["actual_model_calls"] = result["actual_source_calls"] = 0
    result["simulated_service_cost"] = result["service_cost"]
    result["service_cost"] = {
        **result["service_cost"],
        "estimated_upper": 0,
        "reserved_unsettled": 0,
        "status": "simulated",
    }
    result["amap_cost_basis"] = "synthetic_demo_no_network"
    output["markdown"] = (
        "# 合成演示数据\n\n未调用真实服务，地点、路线和资料均为模拟，不可据此旅行。\n\n"
        + output["markdown"]
    )
    return output
