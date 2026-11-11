import { afterEach, expect, test, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST, DELETE } from "../app/api/[...path]/route";

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

test("deletion and cancellation are fixed resource routes with the same origin guard", async () => {
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  const id = "11111111-1111-4111-8111-111111111111";
  const fetcher = vi.fn(async () => Response.json({ deleted: true }));
  vi.stubGlobal("fetch", fetcher);
  const path = `tasks/${id}`;
  expect(
    (
      await DELETE(
        request(path, {
          method: "DELETE",
          headers: { origin: "https://evil.example" },
        }),
        context(path),
      )
    ).status,
  ).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
  expect(
    (await DELETE(request(path, { method: "DELETE" }), context(path))).status,
  ).toBe(200);
  expect(fetcher.mock.calls).toHaveLength(1);
  expect(
    (
      await POST(
        request(`${path}/versions`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: "{}",
        }),
        context(`${path}/versions`),
      )
    ).status,
  ).toBe(404);
});

test("bridge secret remains server-side and browser gets an HttpOnly session", async () => {
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    expect(new Headers(options.headers).get("x-travel-bridge")).toBe(token);
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

test("Vercel requires fixed HTTPS origins and sets a secure session cookie", async () => {
  vi.stubEnv("VERCEL", "1");
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  vi.stubEnv("TRAVEL_PUBLIC_ORIGIN", "https://travel.example");
  vi.stubEnv("TRAVEL_UPSTREAM_URL", "https://api.travel.example");
  const fetcher = vi.fn(async (_url: string) =>
    Response.json({ live_enabled: true }),
  );
  vi.stubGlobal("fetch", fetcher);
  const headers = { host: "travel.example", origin: "https://travel.example" };
  const response = await GET(request("config", { headers }), context("config"));
  expect(response.status).toBe(200);
  expect(response.headers.get("set-cookie")).toContain("Secure");
  expect(fetcher.mock.calls[0][0]).toBe(
    "https://api.travel.example/v1/web/config",
  );
  const foreign = await GET(
    request("config", {
      headers: { ...headers, origin: "https://foreign.example" },
    }),
    context("config"),
  );
  expect(foreign.status).toBe(403);
  expect((await GET(request(), context("config"))).status).toBe(403);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("hosted requests refuse a synthetic backend and never return a saved demo", async () => {
  vi.stubEnv("VERCEL", "1");
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  vi.stubEnv("TRAVEL_PUBLIC_ORIGIN", "https://travel.example");
  vi.stubEnv("TRAVEL_UPSTREAM_URL", "https://api.travel.example");
  const host = { headers: { host: "travel.example" } };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ demo_mode: true, live_enabled: false })),
  );
  const config = await GET(request("config", host), context("config"));
  expect(config.status).toBe(503);
  expect(await config.json()).toEqual({ error: "cloud_live_required" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ output: { is_demo: true, secret_fixture: "demo text" } }),
    ),
  );
  const path = "tasks/11111111-1111-4111-8111-111111111111";
  const result = await GET(request(path, host), context(path));
  expect(result.status).toBe(404);
  expect(await result.text()).not.toContain("demo text");
});

test.each([
  "",
  "http://api.travel.example",
  "https://user:secret@api.travel.example",
  "https://api.travel.example/path",
  "https://travel.example",
])(
  "Vercel fails closed with an invalid backend configuration (%s)",
  async (upstream) => {
    vi.stubEnv("VERCEL", "1");
    vi.stubEnv("TRAVEL_WEB_TOKEN", token);
    vi.stubEnv("TRAVEL_PUBLIC_ORIGIN", "https://travel.example");
    vi.stubEnv("TRAVEL_UPSTREAM_URL", upstream);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect((await GET(request(), context("config"))).status).toBe(503);
    expect(fetcher).not.toHaveBeenCalled();
  },
);

test("Vercel forwards only its HMAC network tag and keeps a saved key off task requests", async () => {
  vi.stubEnv("VERCEL", "1");
  vi.stubEnv("TRAVEL_WEB_TOKEN", token);
  vi.stubEnv("TRAVEL_PUBLIC_ORIGIN", "https://travel.example");
  vi.stubEnv("TRAVEL_UPSTREAM_URL", "https://api.travel.example");
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    expect(options.body).toBe("{}");
    return Response.json({ has_user_key: true });
  });
  vi.stubGlobal("fetch", fetcher);
  const key = "sk-synthetic-user-test-key";
  const post = (path: string, ip: string) =>
    POST(
      request(path, {
        method: "POST",
        body: "{}",
        headers: {
          host: "travel.example",
          origin: "https://travel.example",
          "content-type": "application/json",
          "x-vercel-forwarded-for": ip,
          "x-travel-network": "untrusted-browser-input",
          "x-travel-deepseek-key": key,
        },
      }),
      context(path),
    );
  await post("credentials", "2001:db8::1");
  await post("tasks", "2001:db8::1");
  await post("tasks", "2001:db8::2");
  const [credential, one, two] = fetcher.mock.calls.map(
    ([, options]) => new Headers(options.headers),
  );
  expect(credential.get("x-travel-deepseek-key")).toBe(key);
  expect(one.get("x-travel-deepseek-key")).toBeNull();
  expect(one.get("x-travel-network")).toMatch(/^[0-9a-f]{64}$/);
  expect(one.get("x-travel-network")).toBe(two.get("x-travel-network"));
});
