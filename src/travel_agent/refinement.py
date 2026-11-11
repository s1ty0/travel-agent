"""Explicit, bounded local edits. Day-scoped language never changes the whole trip silently."""

import re
from dataclasses import dataclass

from .deepseek import ProviderError
from .parsing import parse_budget
from .planning_models import DayOverride, DayWindow, PlanningInput


def number(text):
    if text.isdigit():
        return int(text)
    values = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if "十" in text:
        high, low = text.split("十")
        return values.get(high, 1) * 10 + values.get(low, 0)
    return values.get(text, -1)


@dataclass(frozen=True)
class LocalEdit:
    planning: PlanningInput
    affected_days: tuple[int, ...]
    note: str


def structured_edit(planning, base):
    if (
        planning.city != base.city
        or planning.references != base.references
        or planning.page_urls != base.page_urls
        or planning.constraints.required_places != base.constraints.required_places
    ):
        return None
    excluded = {"budget", "budget_scope", "cap_confirmed"}
    if planning.trip.model_dump(exclude=excluded) != base.trip.model_dump(exclude=excluded):
        return None
    return LocalEdit(
        planning,
        tuple(range(1, (planning.trip.days or 1) + 1)),
        "按明确条件重排，复用上一版依据，未重新调用模型研究。",
    )


def parse_local_edit(text, base, base_output):
    fragments = [part.strip() for part in re.split(r"[，,。；;]", text) if part.strip()]
    planning = base.model_copy(deep=True)
    affected, notes = set(), []
    activities = ((base_output.get("result") or {}).get("plan") or {}).get("activities", [])
    for fragment in fragments:
        fragment = re.sub(r"^(?:请把|请|把)", "", fragment).strip()
        day_match = re.match(r"^第([一二三123])天", fragment)
        day = number(day_match[1]) if day_match else None
        command = fragment[day_match.end() :].strip() if day_match else fragment
        if day is not None and (planning.trip.days is None or day > planning.trip.days):
            raise ProviderError("day_override_outside_trip")
        override = next(
            (item for item in planning.day_overrides if item.day == day),
            DayOverride(day=day) if day else None,
        )
        end = re.fullmatch(
            r"(?:在)?(上午|下午|晚上|凌晨)?([\d一二两三四五六七八九十]+)点(半)?(?:结束|返程)",
            command,
        )
        if end:
            hour = number(end[2])
            if end[1] in {"下午", "晚上"} and 1 <= hour < 12:
                hour += 12
            if end[1] == "晚上" and hour == 12:
                hour = 24
            active_window = (
                override.window
                if override and override.window
                else planning.day_window or DayWindow()
            )
            window = DayWindow(
                start_minute=active_window.start_minute,
                end_minute=hour * 60 + (30 if end[3] else 0),
            )
            if day:
                override = override.model_copy(update={"window": window})
            else:
                planning = planning.model_copy(update={"day_window": window})
        elif command in {"少走路", "安排轻松一点", "减少步行"}:
            if day:
                override = override.model_copy(update={"max_walk_minutes": 45})
            else:
                planning = planning.model_copy(
                    update={
                        "constraints": planning.constraints.model_copy(
                            update={"max_walk_minutes": 45}
                        )
                    }
                )
            notes.append("少走路暂按每天最多45分钟步行，可在条件栏进一步调整。")
        elif re.fullmatch(r"(?:换掉|去掉|删掉|不要去).+", command):
            name = re.sub(r"^(?:换掉|去掉|删掉|不要去)", "", command).strip()
            matches = {
                a["place_name"]
                for a in activities
                if name in a["place_name"] and (day is None or a["day"] == day)
            }
            if len(matches) != 1:
                raise ProviderError("ambiguous_place_change")
            name = next(iter(matches))
            planning = planning.model_copy(
                update={
                    "constraints": planning.constraints.model_copy(
                        update={
                            "required_places": tuple(
                                item
                                for item in planning.constraints.required_places
                                if item not in name
                            )
                        }
                    )
                }
            )
            if day:
                override = override.model_copy(
                    update={
                        "excluded_places": tuple(dict.fromkeys((*override.excluded_places, name)))
                    }
                )
            else:
                planning = planning.model_copy(
                    update={
                        "constraints": planning.constraints.model_copy(
                            update={
                                "excluded_places": tuple(
                                    dict.fromkeys((*planning.constraints.excluded_places, name))
                                )
                            }
                        )
                    }
                )
            notes.append("已移除指定地点；替代地点需要另行选择，本次不编造替代项。")
        elif re.fullmatch(r"(?:总)?预算(?:改为|改成|换为|调整为)?\s*.+", command) and day is None:
            budget = re.sub(r"^(?:总)?预算(?:改为|改成|换为|调整为)?\s*", "", command)
            parse_budget(budget)
            planning = planning.model_copy(
                update={
                    "trip": planning.trip.model_copy(
                        update={"budget": budget, "budget_scope": None, "cap_confirmed": False}
                    )
                }
            )
        else:
            if day_match:
                raise ProviderError("unsupported_local_refinement")
            return None
        if day:
            planning = planning.model_copy(
                update={
                    "day_overrides": tuple(
                        sorted(
                            [item for item in planning.day_overrides if item.day != day]
                            + [override],
                            key=lambda item: item.day,
                        )
                    )
                }
            )
            affected.add(day)
        else:
            affected.update(range(1, (planning.trip.days or 1) + 1))
    if not fragments:
        return None
    if len(planning.trip.raw_text) + len(text) + 1 > 4000:
        notes.append("偏好上下文仅保留最近4000字符；原输入仍可在各版本中追溯。")
    trip = planning.trip.model_copy(
        update={"raw_text": (planning.trip.raw_text + "\n" + text)[-4000:]}
    )
    # Revalidate all nested updates; model_copy alone deliberately does not validate changes.
    planning = PlanningInput.model_validate(
        {**planning.model_dump(mode="json"), "trip": trip.model_dump(mode="json")}
    )
    return LocalEdit(
        planning, tuple(sorted(affected)), "；".join(notes) or "只调整本次明确提到的条件。"
    )
