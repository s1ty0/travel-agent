"""Version-bound time shifts; all activity content and durations stay server-owned."""

from typing import Annotated

from pydantic import Field, model_validator

from .deepseek import ProviderError
from .models import Contract
from .planning_models import Proposal
from .references import digest

VERSION = "time-patch-v1"


class TimeShift(Contract):
    activity_index: Annotated[int, Field(strict=True, ge=0, le=8)]
    start_minute: Annotated[int, Field(strict=True, ge=0, le=1439)]


class TimePatch(Contract):
    base_proposal_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    shifts: Annotated[tuple[TimeShift, ...], Field(min_length=1, max_length=9)]

    @model_validator(mode="after")
    def unique_indices(self):
        indices = [item.activity_index for item in self.shifts]
        if len(set(indices)) != len(indices):
            raise ValueError("duplicate_time_shift_index")
        return self


def proposal_hash(proposal):
    return digest(proposal.model_dump_json())


def apply_time_patch(original: Proposal, patch: TimePatch):
    if patch.base_proposal_sha256 != proposal_hash(original):
        raise ProviderError("time_patch_base_mismatch")
    activities = list(original.activities)
    for shift in patch.shifts:
        if shift.activity_index >= len(activities):
            raise ProviderError("time_patch_activity_unknown")
        activity = activities[shift.activity_index]
        end = shift.start_minute + activity.end_minute - activity.start_minute
        if end > 1440:
            raise ProviderError("time_patch_out_of_day")
        activities[shift.activity_index] = activity.model_copy(
            update={"start_minute": shift.start_minute, "end_minute": end}
        )
    return Proposal(activities=tuple(activities))


def time_patch_tool():
    return {
        "type": "function",
        "function": {
            "name": "patch_plan_times",
            "description": (
                "只移动原提交中指定索引的开始分钟。复制反馈的base_proposal_sha256；"
                "索引按原activities顺序从0起，未列项不动，结束时间由服务端按原时长计算。"
                "保留活动、日期、地点、类型、引用和主张；不能删除或缩短活动。"
            ),
            "parameters": TimePatch.model_json_schema(),
        },
    }
