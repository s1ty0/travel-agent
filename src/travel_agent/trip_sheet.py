"""Structured trip handoff for the web view and export."""

from datetime import timedelta


def trip_sheet(planning, plan):
    trip = planning.trip
    days = trip.days
    if days is None and trip.start_date and trip.end_date:
        days = (trip.end_date - trip.start_date).days + 1
    day_dates = (
        {
            str(day): (trip.start_date + timedelta(days=day - 1)).isoformat()
            for day in range(1, (days or 0) + 1)
        }
        if trip.start_date
        else {}
    )
    todo = []
    if not trip.start_date or not trip.adults:
        missing = [
            label
            for label, value in (("出发日期", trip.start_date), ("出行人数", trip.adults))
            if not value
        ]
        todo.append("补充" + "、".join(missing) + "。")
    if not trip.origin or trip.origin != planning.city:
        todo.append("确认出发地、到达时间和返程班次，再核对首末日安排。")
    if (days or 0) > 1:
        todo.append("如需过夜，确认住宿地址、入住时间与价格。")
    todo.append("出发前查景点官方通知与预约入口，确认餐厅营业时间。")
    if any(c["rule"] == "transfer_duration" and c["status"] == "unknown" for c in plan["checks"]):
        todo.append("核对未估算的市内转场，确认有足够时间。")
    if plan.get("travel_budget", {}).get("status") != "within_estimated_cap":
        todo.append("补齐门票、餐饮、交通和适用的住宿报价，核对总预算。")
    return {
        "city": planning.city,
        "origin": trip.origin,
        "dates": day_dates,
        "days": days,
        "adults": trip.adults,
        "budget": trip.budget,
        "todo": todo,
    }
