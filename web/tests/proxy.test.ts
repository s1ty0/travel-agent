import { afterEach, expect, test, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST } from "../app/api/[...path]/route";

const token = "synthetic-server-secret-0123456789";
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
function request(
  path = "config",
  options: NonNullable<ConstructorParameters<typeof NextRequest>[1]> = {},
) {
  const headers = new Headers(options.headers);
  if (!headers.has("host")) headers.set("host", "127.0.0.1:3000");
  return new NextRequest(`http://127.0.0.1:3000/api/${path}`, {
    ...options,
    headers,
  });
}
const context = (path: string) => ({
  params: Promise.resolve({ path: path.split("/") }),
});

test("bridge secret remains server-side and browser gets an HttpOnly session", async () => {
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    expect(options.headers).toMatchObject({ "X-Travel-Bridge": token });
    return Response.json({ live_enabled: true });
  });
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request(), context("config"));
  expect(response.status).toBe(200);
  expect(response.headers.get("set-cookie")).toContain("HttpOnly");
  expect(response.headers.get("set-cookie")).toContain("SameSite=strict");
  expect(await response.text()).not.toContain(token);
});

test("cross-site origin and unknown hosts cannot create paid tasks", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const foreign = request("tasks", {
    method: "POST",
    headers: {
      origin: "https://evil.example",
      "content-type": "application/json",
    },
    body: "{}",
  });
  expect((await POST(foreign, context("tasks"))).status).toBe(403);
  const badHost = request("tasks", {
    method: "POST",
    headers: { host: "evil.example", "content-type": "application/json" },
    body: "{}",
  });
  expect((await POST(badHost, context("tasks"))).status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});

test("bridge forwards only fixed paths and refuses non-JSON or oversized input", async () => {
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await GET(request("https:evil"), context("https:evil"))).status).toBe(
    404,
  );
  expect(
    (
      await POST(
        request("tasks", { method: "POST", body: "x" }),
        context("tasks"),
      )
    ).status,
  ).toBe(415);
  expect(
    (
      await POST(
        request("tasks", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: "x".repeat(17000),
        }),
        context("tasks"),
      )
    ).status,
  ).toBe(413);
  expect(fetcher).not.toHaveBeenCalled();
});

test("network errors do not expose exception text or trigger retries", async () => {
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  const fetcher = vi.fn(async () => {
    throw new Error("private credential response");
  });
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request(), context("config"));
  expect(await response.json()).toEqual({ error: "backend_unavailable" });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
