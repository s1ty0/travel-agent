from datetime import date

from travel_agent.deepseek import DeepSeekSettings
from travel_agent.models import IntakeInput
from travel_agent.open_map import OpenMap
from travel_agent.plan_render import render_markdown
from travel_agent.planning_models import DayWindow, PlanningInput
from travel_agent.product_planner import ProductPlanner
from travel_agent.service_budget import PriceCard, ServiceBudget
from travel_agent.source_models import NearbyQuery, PlaceQuery, WalkingQuery
from travel_agent.synthetic import SyntheticProviders
from travel_agent.trip_sheet import trip_sheet


def test_suzhou_real_place_and_foot_graph_have_separate_provenance():
    provider = OpenMap("苏州")
    places = provider(PlaceQuery(city="苏州", keywords="风景名胜", count=6))
    assert places.provider == "openstreetmap"
    first = next(place for place in places.hits if place.name == "拙政园")
    second = next(place for place in places.hits if place.name == "狮子林")
    assert first.poi_id.startswith(("node/", "way/")) and first.coordinate.system == "WGS84"
    foods = provider(NearbyQuery(keywords="餐厅", center=first.coordinate, radius_meters=1500))
    assert foods.hits and all(not item.name.startswith("演示") for item in foods.hits)
    path = provider(WalkingQuery(origin=first.coordinate, destination=second.coordinate))
    assert path.hits and path.hits[0].distance_meters > 100


def test_open_map_product_export_has_dates_real_restaurants_and_explicit_unknowns():
    fixture = SyntheticProviders()
    runner = ProductPlanner(
        DeepSeekSettings("test-key"),
        None,
        PriceCard(),
        today=date(2026, 11, 11),
        map_factory=OpenMap,
        model_transport=fixture.model_transport,
        source_transport=fixture.source_transport,
    )
    planning = PlanningInput(
        city="苏州",
        day_window=DayWindow(),
        trip=IntakeInput(
            destination="苏州",
            origin="上海",
            days=1,
            adults=2,
            start_date=date(2026, 11, 21),
            budget="1500以内",
        ),
    )
    result = runner.run(planning, ledger=ServiceBudget())
    assert result["status"] == "draft"
    assert {batch["provider"] for batch in result["sources"]} == {
        "openstreetmap",
        "deepseek_search",
    }
    assert sum(item["kind"] == "meal" for item in result["plan"]["activities"]) == 2
    assert not any(item["place_name"].startswith("演示") for item in result["plan"]["activities"])
    result["trip_sheet"] = trip_sheet(planning, result["plan"])
    report = render_markdown(result)
    for term in ("旅行行程单（待核实）", "2026-11-21", "出发前清单", "OpenStreetMap contributors"):
        assert term in report
