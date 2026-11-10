import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Planner from "../components/Planner";
import { ResultPanel } from "../components/ResultPanel";
import { safeLink } from "../lib/contracts";
import { completed } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

test("initial page has no invented itinerary and does not call paid task creation", async () => {
  const fetcher = vi.fn(async () => Response.json({ live_enabled: true }));
  vi.stubGlobal("fetch", fetcher);
  render(<Planner />);
  await screen.findByText("服务已连接");
  expect(screen.getByText("你的下一站，听你的。")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("合成测试馆")).not.toBeInTheDocument();
});

test("natural question requires visible condition confirmation before paid creation", async () => {
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
  fireEvent.click(screen.getByRole("button", { name: "确认条件并生成" }));
  await screen.findByText("合成测试馆");
  expect(submitted?.planning).toMatchObject({
    city: "南京",
    trip: { destination: "南京", budget_scope: "total" },
  });
  expect(screen.getByText("尚不能确认")).toBeInTheDocument();
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
