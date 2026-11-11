import { test, expect } from "@playwright/test";

test.use({ trace: "off" }); // Never upload anonymous capability cookies in traces.
test("actual offline API and SQLite support first draft, refinement, refresh and deletion", async ({
  page,
}) => {
  await page.goto("/");
  const config = await page.request.get("/api/config");
  expect(config.ok()).toBeTruthy();
  const settings = await config.json();
  expect(settings.demo_mode).toBe(true);
  expect(settings.live_enabled).toBe(false); // Fail before any POST if a live server was reused.
  await page.getByLabel("描述你的旅行").fill("杭州两天，预算2000以内");
  await page.getByRole("button", { name: "发送旅行想法" }).click();
  await expect(page.getByText("合成演示数据 · 未调用真实服务")).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByText("演示费用 ¥0 · 无真实服务调用")).toBeVisible();
  await page.getByLabel("描述你的旅行").fill("第二天下午五点结束");
  await page.getByRole("button", { name: "发送旅行想法" }).click();
  await expect(page.getByText("本版修改 · v2")).toBeVisible({ timeout: 30000 });
  await page.reload();
  await expect(page.getByText("本版修改 · v2")).toBeVisible();
  await page.getByRole("tab", { name: /来源与依据/ }).click();
  await expect(page.getByText("合成演示数据 · 未调用真实服务")).toBeVisible();
  await expect(page.getByRole("link", { name: "合成旅行资料" })).toHaveCount(0);
  await page.getByRole("tab", { name: "行程安排" }).click();
  await page.screenshot({
    path: `test-results/${test.info().project.name}-fullstack-demo.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "删除这次旅行" }).click();
  await page.getByRole("button", { name: "确认删除全部版本" }).click();
  await expect(
    page.getByText("生成第一份行程后，会保存在这里。"),
  ).toBeVisible();
});
