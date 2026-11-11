/** Browser-facing subset of the current planning output. No supplier secrets. */
export type Check = {
  rule: string;
  status: "pass" | "fail" | "unknown";
  required_minutes?: number;
  available_minutes?: number;
  missing_days?: number[];
  expected_days?: number;
};
export type ScheduleBlock = {
  kind: string;
  start_minute: number;
  end_minute: number;
  activity_index?: number;
  title?: string;
  note?: string;
};
export type DaySchedule = {
  day: number;
  blocks: ScheduleBlock[];
  missing_day: boolean;
  missing_meals: string[];
  has_open_time: boolean;
};
export type Activity = {
  day: number;
  place_source_id: string;
  place_name: string;
  address?: string;
  start_minute: number;
  end_minute: number;
  kind: string;
  citations?: { source_id: string; quote: string }[];
  claims?: { predicate: string; value: boolean }[];
};
export type Result = {
  run_id: string;
  status: string;
  research_mode?: string;
  issue?: string;
  model_calls: number;
  source_calls: number;
  replans: number;
  elapsed_ms: number;
  service_cost: {
    estimated_upper: number;
    cap: number;
    status: string;
    reserved_unsettled: number;
  };
  plan: null | {
    draft_note?: string;
    draft_method?: string;
    activities: Activity[];
    day_schedule?: DaySchedule[];
    day_window?: { start_minute: number; end_minute: number };
    day_windows?: Record<string, { start_minute: number; end_minute: number }>;
    checks: Check[];
    facts_verified: boolean;
    missing_fields: string[];
    travel_budget: { status: string };
  };
  sources: {
    provider: string;
    status: string;
    retrieved_at?: string;
    hits: {
      source_id: string;
      title?: string;
      name?: string;
      url?: string;
      snippet?: string;
      evidence_kind?: string;
    }[];
  }[];
};
export type Task = {
  task_id: string;
  state: "running" | "completed" | "failed" | "cancelled" | "interrupted";
  stage?: string;
  steps?: number;
  version?: number;
  root_id?: string;
  error: string | null;
  output: null | {
    city: string;
    assumptions?: string[];
    planning?: {
      city: string;
      day_window?: { start_minute: number; end_minute: number } | null;
      day_overrides?: {
        day: number;
        window?: { start_minute: number; end_minute: number } | null;
        max_walk_minutes?: number | null;
        excluded_places?: string[];
      }[];
      trip: {
        budget?: string | null;
        days?: number | null;
        adults?: number | null;
        origin?: string | null;
        start_date?: string | null;
        raw_text?: string;
      };
      constraints?: {
        max_sightseeing_places?: number | null;
        max_walk_minutes?: number | null;
        required_places?: string[];
        excluded_places?: string[];
      };
    } | null;
    result: Result;
    markdown: string;
    audit_saved: boolean;
    change_summary?: {
      added: number;
      removed: number;
      time_changed: number;
      conditions_changed: boolean;
    };
  };
};
export const errorText: Record<string, string> = {
  backend_unavailable: "规划服务暂未连接，请检查后端是否启动。",
  web_not_configured: "网页服务尚未配置，请使用项目启动脚本。",
  live_planning_disabled: "真实规划尚未开启，请使用启动脚本的 -Live 参数。",
  planner_busy: "已有规划正在生成，请稍后再试。",
  task_not_found: "该任务已到期、被删除，或不属于当前私有会话。",
  planning_unavailable:
    "这次规划未能完成，没有自动重试。请检查服务配置后再试。",
  invalid_planning_input: "请检查目的地、日期、人数和预算，部分条件可能冲突。",
  service_budget_exhausted: "服务调用预算不足，已停止后续调用。",
  source_call_limit: "已达到本次来源查询上限。",
  model_call_limit: "已达到本次模型回合上限。",
  invalid_model_or_tool_response:
    "模型返回的安排或查询格式不符合要求，本次已停止，没有自动重试。",
  run_time_limit: "已达到本次规划耗时上限。",
  cancelled: "已取消后续规划。已发出的调用可能产生费用，没有自动重新生成。",
  worker_interrupted_no_retry:
    "规划进程中断，调用费用可能尚未确认，没有自动重新调用。",
  task_removed_no_replay:
    "该请求的结果已删除或到期，重复请求不会重新调用模型。",
  daily_task_quota: "已达到当前会话或服务的每日任务上限。",
  cancel_before_delete: "请先取消正在生成的任务，再删除旅行。",
  unsupported_local_refinement:
    "这条单日修改暂无法准确理解。可输入“第二天下午五点结束”或在条件栏调整。",
  ambiguous_place_change: "无法唯一确定要移除的地点，请使用行程中的完整名称。",
  day_override_outside_trip: "要修改的天数不在当前行程范围内。",
  base_conditions_unavailable: "上一版条件尚不可用，请先填写目的城市等条件。",
  select_exact_destination_and_1_to_3_days:
    "请补充一个目的城市和1～3天的时长，已有想法已保留在条件栏。",
};
export function minute(value: number) {
  return `${Math.floor(value / 60)
    .toString()
    .padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
}
export function safeLink(value?: string) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}
export const ruleText: Record<string, string> = {
  sightseeing_place_limit: "景点数量",
  time_overlap: "时间重叠",
  transfer_duration: "交通衔接",
  day_coverage: "逐日覆盖",
  daily_activity_limit: "每天地点活动数",
  claim_evidence_review: "主张依据",
  repair_scope_violation: "修正范围",
  reference_quote: "资料引用",
  activity_time_window: "每日起止时间",
  daily_schedule_details: "用餐与自由时段待细化",
  daily_walking_limit: "每日步行上限",
  required_place: "必去地点",
  required_activity_fit: "必去活动时间不足",
  activity_not_fitted: "部分可选活动未能排入",
};

export function stageText(stage?: string) {
  return (
    (
      {
        queued: "正在排队",
        starting: "准备规划",
        idea_extraction: "理解旅行想法",
        search_web: "查找旅行资料",
        find_places: "查询候选地点",
        nearby_food: "查找附近餐厅",
        combined_research: "整理旅行候选",
        experience_agent: "研究景点安排",
        food_agent: "研究餐饮偏好",
        walking_route: "核对步行路线",
        cancelling: "正在停止后续调用",
      } as Record<string, string>
    )[stage ?? ""] ?? "正在整理行程"
  );
}

export function checkDescription(check: Check) {
  if (check.rule === "day_coverage" && check.missing_days?.length)
    return `第${check.missing_days.join("、")}天尚未安排，需要补齐请求的${check.expected_days ?? "全部"}天行程。`;
  if (
    check.rule === "transfer_duration" &&
    check.required_minutes !== undefined
  )
    return `交通衔接需${check.required_minutes}分钟，当前间隔${check.available_minutes ?? "未知"}分钟。`;
  return ruleText[check.rule] ?? check.rule;
}
