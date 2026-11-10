import { test, expect } from "@playwright/test";
import { completed } from "../tests/fixtures";

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
  await page.getByRole("button", { name: "确认条件并生成" }).click();
  await expect(page.getByText("合成测试馆")).toBeVisible();
  await expect(page.getByText("尚不能确认")).toBeVisible();
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
  expect(creations).toBe(1);
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
  await expect(page.getByRole("alert")).toBeVisible();
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
