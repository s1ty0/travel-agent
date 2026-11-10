/** Browser-facing subset of the current planning output. No supplier secrets. */
export type Check = {
  rule: string;
  status: "pass" | "fail" | "unknown";
  required_minutes?: number;
  available_minutes?: number;
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
  state: "running" | "completed" | "failed";
  error: string | null;
  output: null | {
    city: string;
    assumptions?: string[];
    planning?: {
      city: string;
      trip: {
        budget?: string | null;
        days?: number | null;
        adults?: number | null;
        origin?: string | null;
        start_date?: string | null;
        raw_text?: string;
      };
      constraints?: { max_sightseeing_places?: number | null };
    } | null;
    result: Result;
    markdown: string;
    audit_saved: boolean;
  };
};
export const errorText: Record<string, string> = {
  backend_unavailable: "规划服务暂未连接，请检查后端是否启动。",
  web_not_configured: "网页服务尚未配置，请使用项目启动脚本。",
  live_planning_disabled: "真实规划尚未开启，请使用启动脚本的 -Live 参数。",
  planner_busy: "已有规划正在生成，请稍后再试。",
  task_not_found: "这份临时任务已过期或服务已重启，请重新发起。",
  planning_unavailable:
    "这次规划未能完成，没有自动重试。请检查服务配置后再试。",
  invalid_planning_input: "请检查目的地、日期、人数和预算，部分条件可能冲突。",
  service_budget_exhausted: "服务调用预算不足，已停止后续调用。",
  source_call_limit: "已达到本次来源查询上限。",
  model_call_limit: "已达到本次模型回合上限。",
  run_time_limit: "已达到本次规划耗时上限。",
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
  daily_activity_limit: "每天活动数",
  claim_evidence_review: "主张依据",
  repair_scope_violation: "修正范围",
  reference_quote: "资料引用",
};
