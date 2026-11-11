import type { Task } from "../lib/contracts";
/** Synthetic test data only. Never loaded by the website. */
export const completed: Task = {
  task_id: "00000000-0000-4000-8000-000000000001",
  state: "completed",
  error: null,
  output: {
    city: "南京",
    planning: {
      city: "南京",
      trip: { budget: "2000以内", days: 1, raw_text: "南京一日博物馆" },
    },
    assumptions: ["人数尚未明确，费用口径待补充。"],
    audit_saved: true,
    markdown: "# 合成草案\n仅用于测试。",
    result: {
      run_id: "synthetic-run",
      status: "draft",
      model_calls: 3,
      source_calls: 3,
      replans: 0,
      elapsed_ms: 100,
      service_cost: {
        estimated_upper: 10000,
        cap: 2000000,
        status: "estimated",
        reserved_unsettled: 0,
      },
      plan: {
        day_window: { start_minute: 540, end_minute: 1200 },
        day_schedule: [
          {
            day: 1,
            missing_day: false,
            missing_meals: [],
            has_open_time: true,
            blocks: [
              {
                kind: "activity",
                activity_index: 0,
                start_minute: 540,
                end_minute: 600,
              },
              {
                kind: "meal",
                title: "晚餐 · 餐厅待选",
                note: "具体餐厅和价格待查",
                start_minute: 1080,
                end_minute: 1140,
              },
              {
                kind: "open_time",
                title: "晚间自由活动或返程 · 待确认",
                note: "具体地点与交通待补充",
                start_minute: 1140,
                end_minute: 1200,
              },
            ],
          },
        ],
        facts_verified: false,
        missing_fields: ["start_date"],
        travel_budget: { status: "uncertain" },
        checks: [{ rule: "transfer_duration", status: "unknown" }],
        activities: [
          {
            day: 1,
            place_source_id: "synthetic-place",
            place_name: "合成测试馆",
            address: "测试地址",
            start_minute: 540,
            end_minute: 600,
            kind: "sightseeing",
            citations: [{ source_id: "test-web", quote: "来源片段仅供测试" }],
          },
        ],
      },
      sources: [
        {
          provider: "deepseek_search",
          status: "ok",
          hits: [
            {
              source_id: "test-web",
              title: "合成来源",
              url: "https://example.com/source",
              evidence_kind: "search_snippet",
              snippet: "来源片段仅供测试",
            },
          ],
        },
      ],
    },
  },
};
