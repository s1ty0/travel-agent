import { test, expect } from "@playwright/test";
import { completed } from "../tests/fixtures";

test("private history restores versions and deletes the whole trip without creating another plan", async ({
  page,
}) => {
  const firstId = completed.task_id;
  const secondId = "00000000-0000-4000-8000-000000000002";
  const first = { ...structuredClone(completed), version: 1, root_id: firstId };
  const second = {
    ...structuredClone(completed),
    task_id: secondId,
    version: 2,
    root_id: firstId,
  };
  if (!second.output) throw new Error("missing fixture");
  second.output.change_summary = {
    added: 0,
    removed: 1,
    time_changed: 0,
    conditions_changed: true,
  };
  const entries = [second, first].map((value) => ({
    task_id: value.task_id,
    root_id: firstId,
    version: value.version,
    city: "南京",
    state: "completed",
  }));
  let deleted = false,
    creations = 0;
  await page.addInitScript(
    (id) => sessionStorage.setItem("travel_task", id),
    secondId,
  );
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("config"))
      return route.fulfill({
        json: { live_enabled: true, storage: "private_session_7_days" },
      });
    if (route.request().method() === "POST") creations++;
    if (route.request().method() === "DELETE") {
      deleted = true;
      return route.fulfill({ json: { deleted: true } });
    }
    if (path === "/api/tasks")
      return route.fulfill({ json: { tasks: deleted ? [] : [entries[0]] } });
    if (path.endsWith("versions"))
      return route.fulfill({ json: { tasks: entries } });
    return route.fulfill({ json: path.endsWith(secondId) ? second : first });
  });
  await page.goto("/");
  await expect(page.getByText("本版修改 · v2")).toBeVisible();
  await page.getByText("历史版本（2）").click();
  await page.getByRole("button", { name: "查看第1版" }).click();
  await expect(page.getByRole("button", { name: "查看第1版" })).toBeDisabled();
  await page.getByRole("button", { name: "南京 · v2" }).click();
  await expect(page.getByText("本版修改 · v2")).toBeVisible();
  await page.screenshot({
    path: `test-results/${test.info().project.name}-history.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "删除这次旅行" }).click();
  expect(deleted).toBe(false);
  await page.getByRole("button", { name: "确认删除全部版本" }).click();
  await expect(
    page.getByText("生成第一份行程后，会保存在这里。"),
  ).toBeVisible();
  expect(creations).toBe(0);
});

test("cancellation remains terminal after refresh and never creates a replacement task", async ({
  page,
}) => {
  let cancelled = false,
    creations = 0;
  const state = () => ({
    task_id: completed.task_id,
    state: cancelled ? "cancelled" : "running",
    stage: cancelled ? "cancelled" : "combined_research",
    output: null,
    error: null,
  });
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("config"))
      return route.fulfill({ json: { live_enabled: true } });
    if (path.endsWith("cancel")) cancelled = true;
    else if (route.request().method() === "POST") creations++;
    return route.fulfill({ json: state() });
  });
  await page.goto("/");
  await page.getByText("南京 · 博物馆慢游").click();
  await page.getByRole("button", { name: "发送旅行想法" }).click();
  await page.getByRole("button", { name: "取消后续规划" }).click();
  await expect(page.getByText("本次规划已取消")).toBeVisible();
  await page.reload();
  await expect(page.getByText("本次规划已取消")).toBeVisible();
  expect(creations).toBe(1);
});

test("question, confirmation, itinerary, sources, export and refresh", async ({
  page,
}) => {
  let creations = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("config"))
      return route.fulfill({ json: { live_enabled: true } });
    if (route.request().method() === "POST") creations++;
    return route.fulfill({ json: completed });
  });
  await page.goto("/");
  await expect(page.getByText("服务已连接")).toBeVisible();
  await page.getByText("南京 · 博物馆慢游").click();
  await expect(page.getByLabel("目的城市")).toHaveValue("南京");
  await expect(page.getByText("合成测试馆")).toHaveCount(0);
  expect(creations).toBe(0);
  await page.getByRole("button", { name: "发送旅行想法" }).click();
  await expect(page.getByText("合成测试馆")).toBeVisible();
  await expect(page.getByText("尚不能确认")).toBeVisible();
  await expect(page.getByText("晚餐 · 餐厅待选")).toBeVisible();
  await expect(page.getByText("晚间自由活动或返程 · 待确认")).toBeVisible();
  await page.getByLabel("旅行总预算").fill("3000以内");
  await page.getByRole("button", { name: "按补充条件更新行程" }).click();
  await expect(
    page.getByRole("button", { name: "查看上一版行程" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /来源与依据/ }).click();
  await expect(page.getByRole("link", { name: "合成来源" })).toHaveAttribute(
    "href",
    "https://example.com/source",
  );
  await page.getByRole("tab", { name: "检查与缺口" }).click();
  await expect(page.getByText("交通衔接")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  expect((await download).suggestedFilename()).toBe("旅行规划草案.md");
  await page.reload();
  await expect(page.getByText("合成测试馆")).toBeVisible();
  expect(creations).toBe(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `test-results/${test.info().project.name}-result.png`,
    fullPage: true,
  });
});

test("homepage layout and unavailable service", async ({ page }) => {
  await page.route("**/api/config", (route) =>
    route.fulfill({ status: 503, json: { error: "backend_unavailable" } }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("alert").filter({ hasText: "规划服务暂未连接" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `test-results/${test.info().project.name}-home.png`,
    fullPage: true,
  });
});
