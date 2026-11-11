import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Planner from "../components/Planner";
import { ResultPanel } from "../components/ResultPanel";
import { safeLink } from "../lib/contracts";
import { completed } from "./fixtures";
import type { Task } from "../lib/contracts";

afterEach(() => vi.unstubAllGlobals());

test("switching to live ignores an old demo draft and submits a fresh trip", async () => {
  const demo = structuredClone(completed) as Task;
  if (!demo.output) throw new Error("missing fixture output");
  demo.output.result.is_demo = true;
  sessionStorage.setItem("travel_task", demo.task_id);
  let submitted: Record<string, unknown> | undefined;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("config"))
      return Response.json({ live_enabled: true, demo_mode: false });
    if (options?.method === "POST") {
      submitted = JSON.parse(options.body as string);
      return Response.json(completed);
    }
    return Response.json(demo);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Planner />);
  await screen.findByText("真实规划");
  await waitFor(() => expect(sessionStorage.getItem("travel_task")).toBeNull());
  expect(screen.queryByText("合成测试馆")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("描述你的旅行"), {
    target: { value: "杭州一天" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送旅行想法" }));
  await waitFor(() => expect(submitted?.idea).toBe("杭州一天"));
  expect(submitted).not.toHaveProperty("base_task_id");
});

test("initial page has no invented itinerary and does not call paid task creation", async () => {
  const fetcher = vi.fn(async () => Response.json({ live_enabled: true }));
  vi.stubGlobal("fetch", fetcher);
  render(<Planner />);
  await screen.findByText("服务已连接");
  expect(screen.getByText("你的下一站，听你的。")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("合成测试馆")).not.toBeInTheDocument();
});

test("sending an idea creates a first draft before condition refinement", async () => {
  let submitted: Record<string, unknown> | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("config")) return Response.json({ live_enabled: true });
      if (options?.method === "POST") {
        submitted = JSON.parse(options.body as string);
        return Response.json(completed);
      }
      return Response.json(completed);
    }),
  );
  render(<Planner />);
  await screen.findByText("服务已连接");
  fireEvent.click(screen.getByText("南京 · 博物馆慢游"));
  expect(submitted).toBeUndefined();
  expect(screen.getByLabelText("目的城市")).toHaveValue("南京");
  fireEvent.click(screen.getByRole("button", { name: "发送旅行想法" }));
  await screen.findByText("合成测试馆");
  expect(submitted?.idea).toContain("南京");
  expect(submitted?.planning).toBeUndefined();
  expect(screen.getByText("尚不能确认")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("旅行总预算"), {
    target: { value: "3000以内" },
  });
  fireEvent.click(screen.getByRole("button", { name: "按补充条件更新行程" }));
  await waitFor(() =>
    expect(submitted?.planning).toMatchObject({
      city: "南京",
      trip: { budget: "3000以内" },
      constraints: { max_sightseeing_places: null },
      day_window: { start_minute: 540, end_minute: 1200 },
    }),
  );
  expect(screen.getByRole("button", { name: "查看上一版行程" })).toBeVisible();
});

test("network/config failure never falls back to fabricated plans", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ error: "backend_unavailable" }, { status: 503 }),
    ),
  );
  render(<Planner />);
  await screen.findByRole("alert");
  expect(screen.queryByText("合成测试馆")).not.toBeInTheDocument();
});

test("source and unknown checks stay visible and script links cannot be opened", async () => {
  render(<ResultPanel task={completed} />);
  fireEvent.click(screen.getByRole("tab", { name: /来源与依据/ }));
  expect(screen.getByRole("link", { name: "合成来源" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  fireEvent.click(screen.getByRole("tab", { name: "检查与缺口" }));
  expect(screen.getByText("交通衔接")).toBeInTheDocument();
  expect(safeLink("javascript:alert(1)")).toBeUndefined();
  expect(safeLink("http://127.0.0.1/private")).toBeUndefined();
});

test("refresh resumes the same task without a second creation request", async () => {
  sessionStorage.setItem("travel_task", completed.task_id);
  const fetcher = vi.fn(async (...args: [string, RequestInit?]) =>
    Response.json(
      args[0].endsWith("config") ? { live_enabled: true } : completed,
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<Planner />);
  await screen.findByText("合成测试馆");
  expect(
    screen.getByRole("heading", { name: "南京 · 行程草案" }),
  ).toBeVisible();
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      `/api/tasks/${completed.task_id}`,
      expect.objectContaining({ method: "GET" }),
    ),
  );
  expect(
    fetcher.mock.calls.every(([, options]) => options?.method !== "POST"),
  ).toBe(true);
});

test("missing requested days and unfinished evening are visible in the itinerary", () => {
  const task = structuredClone(completed) as Task;
  if (!task.output?.result.plan) throw new Error("missing fixture plan");
  const plan = task.output.result.plan;
  task.output.result.status = "invalid_plan";
  plan.checks = [
    {
      rule: "day_coverage",
      status: "fail",
      expected_days: 2,
      missing_days: [2],
    },
  ];
  plan.day_window = { start_minute: 540, end_minute: 1200 };
  plan.day_schedule = [
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
          start_minute: 1080,
          end_minute: 1140,
          title: "晚餐 · 餐厅待选",
          note: "营业和价格待查询",
        },
        {
          kind: "open_time",
          start_minute: 1140,
          end_minute: 1200,
          title: "晚间自由活动或返程 · 待确认",
          note: "具体地点待选",
        },
      ],
    },
    {
      day: 2,
      missing_day: true,
      missing_meals: [],
      has_open_time: true,
      blocks: [
        {
          kind: "unplanned_day",
          start_minute: 540,
          end_minute: 1200,
          title: "这一天尚未安排",
          note: "缺少本日活动",
        },
      ],
    },
  ];
  render(<ResultPanel task={task} />);
  expect(screen.getByRole("alert")).toHaveTextContent("第2天尚未安排");
  expect(screen.getByText("晚餐 · 餐厅待选")).toBeVisible();
  expect(screen.getByText("晚间自由活动或返程 · 待确认")).toBeVisible();
  expect(screen.getByText("这一天尚未安排")).toBeVisible();
});
