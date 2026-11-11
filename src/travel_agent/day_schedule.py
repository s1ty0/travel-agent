"""Expose requested days, meals and open time while preserving original activities."""

import math

VERSION = "day-frame-v1"


def _block(start, end, kind, title, note):
    return {
        "start_minute": start,
        "end_minute": end,
        "kind": kind,
        "title": title,
        "note": note,
        "status": "suggested_unverified",
    }


def _free(start, end):
    evening = start >= 1140
    return _block(
        start,
        end,
        "open_time",
        "晚间自由活动或返程 · 待确认" if evening else "自由活动与休息 · 待确认",
        "可补充夜游或返程需求，具体地点、开放时间与交通尚未安排。"
        if evening
        else "可选择休息或补充想去的地方，本段尚未安排具体活动。",
    )


def _gap(start, end, *, between, route, covered_meals=()):
    if end <= start:
        return [], set()
    meals = []
    for name, low, high, preferred in (("午餐", 690, 840, 720), ("晚餐", 1050, 1200, 1080)):
        if name in covered_meals:
            continue
        begin = max(start, min(preferred, end - 60))
        if begin >= low and begin + 60 <= min(end, high):
            meals.append((name, begin))
    if between:
        transfer = math.ceil(route.duration_seconds / 60) + 10 if route is not None else None
        if transfer is not None and end - start < transfer + 60 * len(meals):
            meals = []
        label = "、".join(name for name, _ in meals)
        title = f"{label}及转场" if label else "转场与休息"
        note = "餐厅待选择，价格待查。" if meals else "按实际路程安排交通与休息。"
        note += (
            f"已有步行估算含缓冲需{transfer}分钟。"
            if transfer is not None
            else "交通耗时待核实，尚不能保证用餐与转场均按时完成。"
        )
        return [_block(start, end, "meal_transfer" if meals else "transfer_rest", title, note)], {
            name for name, _ in meals
        }
    blocks, cursor = [], start
    for name, begin in meals:
        if cursor < begin:
            blocks.append(_free(cursor, begin))
        blocks.append(
            _block(
                begin,
                begin + 60,
                "meal",
                f"{name} · 餐厅待选",
                "建议就近用餐，具体餐厅、营业、价格与交通仍需查询。",
            )
        )
        cursor = begin + 60
    if cursor < end:
        blocks.append(_free(cursor, end))
    return blocks, {name for name, _ in meals}


def build_day_schedule(activities, days, window, routes, day_windows=None):
    frames = []
    start, end = window["start_minute"], window["end_minute"]
    indexed = sorted(
        enumerate(activities), key=lambda pair: (pair[1]["day"], pair[1]["start_minute"])
    )
    for day in range(1, days + 1):
        active_window = (day_windows or {}).get(str(day), window)
        start, end = active_window["start_minute"], active_window["end_minute"]
        visits = [(index, activity) for index, activity in indexed if activity["day"] == day]
        if not visits:
            frames.append(
                {
                    "day": day,
                    "blocks": [
                        _block(
                            start,
                            end,
                            "unplanned_day",
                            "这一天尚未安排",
                            "缺少本日活动，需要补全景点、用餐、交通和晚间安排。",
                        )
                    ],
                    "missing_day": True,
                    "missing_meals": [],
                    "has_open_time": True,
                }
            )
            continue
        meals = {
            name
            for _, activity in visits
            if activity["kind"] == "meal"
            for name, low, high in (("午餐", 690, 840), ("晚餐", 1050, 1200))
            if activity["start_minute"] < high and activity["end_minute"] > low
        }
        blocks, cursor, previous = [], start, None
        for index, activity in visits:
            gap_end = min(end, activity["start_minute"])
            route = (
                routes.get((previous["place_source_id"], activity["place_source_id"]))
                if previous
                else None
            )
            between = (
                previous is not None and previous["place_source_id"] != activity["place_source_id"]
            )
            filler, supplied = _gap(
                cursor, gap_end, between=between, route=route, covered_meals=meals
            )
            blocks.extend(filler)
            meals.update(supplied)
            blocks.append(
                {
                    "kind": "activity",
                    "activity_index": index,
                    "start_minute": activity["start_minute"],
                    "end_minute": activity["end_minute"],
                }
            )
            cursor = max(cursor, activity["end_minute"])
            previous = activity
        filler, supplied = _gap(cursor, end, between=False, route=None, covered_meals=meals)
        blocks.extend(filler)
        meals.update(supplied)
        expected = {
            name
            for name, low, high in (("午餐", 690, 840), ("晚餐", 1050, 1200))
            if min(end, high) - max(start, low) >= 60
        }
        frames.append(
            {
                "day": day,
                "blocks": blocks,
                "missing_day": False,
                "missing_meals": sorted(expected - meals),
                "has_open_time": any(block["kind"] == "open_time" for block in blocks),
            }
        )
    return frames
