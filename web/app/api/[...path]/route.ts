import { after, type NextRequest, NextResponse } from "next/server";
import { createHmac, randomBytes } from "node:crypto";
import { isIP } from "node:net";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 240;
const allowed =
  /^(config|credentials|tasks(?:\/[a-f0-9-]{36}(?:\/(?:versions|cancel))?)?)$/;
const localHosts = new Set(["127.0.0.1:3000", "localhost:3000"]);
const maxBodyBytes = 16384;

function httpsOrigin(value: string | undefined): URL | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (
      url.protocol === "https:" &&
      (value === url.origin || value === `${url.origin}/`)
    )
      return url;
  } catch {
    return undefined;
  }
}

function bridgeSettings(host: string) {
  const deployment =
    process.env.VERCEL === "1" && process.env.VERCEL_URL
      ? httpsOrigin(`https://${process.env.VERCEL_URL}`)
      : undefined;
  const publicOrigin = process.env.TRAVEL_PUBLIC_ORIGIN ?? deployment?.origin;
  const upstreamOrigin = process.env.TRAVEL_UPSTREAM_URL;
  if (process.env.VERCEL === "1" || publicOrigin || upstreamOrigin) {
    const canonical = httpsOrigin(publicOrigin);
    const api = httpsOrigin(upstreamOrigin);
    if (!canonical || !api || canonical.origin === api.origin) return undefined;
    const site = deployment?.host === host ? deployment : canonical;
    if (site.origin === api.origin) return undefined;
    return {
      allowed: site.host === host,
      origin: site.origin,
      upstream: api.origin,
      secure: true,
    };
  }
  return {
    allowed: localHosts.has(host),
    origin: `http://${host}`,
    upstream: "http://127.0.0.1:8000",
    secure: false,
  };
}

function dispatchWorker(upstream: string, headers: Headers, id: string) {
  if (!/^[a-f0-9-]{36}$/.test(id)) return;
  after(async () => {
    try {
      await fetch(`${upstream}/v1/web/tasks/${id}/work`, {
        method: "POST",
        headers,
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(225000),
      });
    } catch {
      // A queued task can be picked up on the next poll. Started tasks are never replayed.
    }
  });
}

function networkTag(req: NextRequest, token: string) {
  if (process.env.VERCEL !== "1") return undefined;
  const address = req.headers.get("x-vercel-forwarded-for")?.trim() ?? "";
  const version = isIP(address);
  if (!version || address.includes("%")) return undefined;
  let network = address;
  if (version === 6) {
    const canonical = new URL(`http://[${address}]`).hostname.slice(1, -1);
    const [left, right] = canonical.split("::");
    const head = left ? left.split(":") : [];
    const tail = right ? right.split(":") : [];
    const parts =
      right === undefined
        ? head
        : [...head, ...Array(8 - head.length - tail.length).fill("0"), ...tail];
    if (parts.slice(0, 6).join(":") === "0:0:0:0:0:ffff") {
      const high = Number.parseInt(parts[6], 16);
      const low = Number.parseInt(parts[7], 16);
      network = [high >> 8, high & 255, low >> 8, low & 255].join(".");
    } else {
      network = `${parts.slice(0, 4).join(":")}::/64`;
    }
  }
  return createHmac("sha256", token)
    .update(`travel-trial-network:${network}`)
    .digest("hex");
}

async function readBody(req: NextRequest) {
  if (Number(req.headers.get("content-length") ?? 0) > maxBodyBytes)
    return null;
  if (!req.body) return "";
  const reader = req.body.getReader();
  const chunks: Buffer[] = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return Buffer.concat(chunks).toString("utf8");
      size += value.byteLength;
      if (size > maxBodyBytes) {
        await reader.cancel();
        return null;
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
}

async function forward(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const host = req.headers.get("host") ?? "";
  const origin = req.headers.get("origin");
  const bridge = bridgeSettings(host);
  if (!bridge) {
    return NextResponse.json({ error: "web_not_configured" }, { status: 503 });
  }
  if (!bridge.allowed || (origin && origin !== bridge.origin)) {
    return NextResponse.json({ error: "origin_not_allowed" }, { status: 403 });
  }
  if (
    req.method === "POST" &&
    req.headers.get("content-type") !== "application/json"
  ) {
    return NextResponse.json({ error: "json_required" }, { status: 415 });
  }
  const path = (await context.params).path.join("/");
  const canPost =
    path === "tasks" ||
    path === "credentials" ||
    /^tasks\/[a-f0-9-]{36}\/cancel$/.test(path);
  const canDelete =
    path === "credentials" || /^tasks\/[a-f0-9-]{36}$/.test(path);
  const canRead = path !== "credentials" && !path.endsWith("/cancel");
  if (
    !allowed.test(path) ||
    (req.method === "POST" && !canPost) ||
    (req.method === "DELETE" && !canDelete) ||
    (req.method === "GET" && !canRead)
  ) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  const token = process.env.TRAVEL_WEB_TOKEN;
  if (!token || token.length < 32) {
    return NextResponse.json({ error: "web_not_configured" }, { status: 503 });
  }
  const cookie = req.cookies.get("travel_session")?.value;
  const session =
    cookie && /^[a-f0-9]{64}$/.test(cookie)
      ? cookie
      : randomBytes(32).toString("hex");
  try {
    const body = req.method === "POST" ? await readBody(req) : undefined;
    if (body === null) {
      return NextResponse.json({ error: "input_too_large" }, { status: 413 });
    }
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Travel-Bridge": token,
      "X-Travel-Session": session,
    });
    const bypass = process.env.TRAVEL_UPSTREAM_VERCEL_BYPASS;
    if (bridge.secure && bypass)
      headers.set("X-Vercel-Protection-Bypass", bypass);
    if (req.method === "POST" && path === "credentials") {
      const key = req.headers.get("x-travel-deepseek-key");
      if (!key || !/^sk-[a-zA-Z0-9_-]{12,160}$/.test(key)) {
        return NextResponse.json(
          { error: "invalid_deepseek_key_format" },
          { status: 422 },
        );
      }
      headers.set("X-Travel-DeepSeek-Key", key);
    }
    if (req.method === "POST" && path === "tasks" && bridge.secure) {
      headers.set("X-Travel-Cloud", "1");
      const network = networkTag(req, token);
      if (network) headers.set("X-Travel-Network", network);
    }
    const upstream = await fetch(`${bridge.upstream}/v1/web/${path}`, {
      method: req.method,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10000),
      headers,
    });
    const data = await upstream.json();
    if (bridge.secure && upstream.ok) {
      if (path === "config" && (data.demo_mode || !data.live_enabled)) {
        return NextResponse.json(
          { error: "cloud_live_required" },
          { status: 503 },
        );
      }
      if (data.output?.is_demo || data.output?.result?.is_demo) {
        return NextResponse.json({ error: "task_not_found" }, { status: 404 });
      }
      if (Array.isArray(data.tasks)) {
        data.tasks = data.tasks.filter(
          (entry: { is_demo?: boolean }) => !entry.is_demo,
        );
      }
    }
    if (
      bridge.secure &&
      process.env.TRAVEL_VERCEL_WORKER === "1" &&
      upstream.ok &&
      data.stage === "queued" &&
      typeof data.task_id === "string" &&
      ((req.method === "POST" && path === "tasks") ||
        (req.method === "GET" && /^tasks\/[a-f0-9-]{36}$/.test(path)))
    ) {
      dispatchWorker(bridge.upstream, headers, data.task_id);
    }
    const response = NextResponse.json(
      upstream.ok
        ? data
        : {
            error:
              typeof data.detail === "string"
                ? data.detail
                : "invalid_planning_input",
          },
      { status: upstream.status, headers: { "Cache-Control": "no-store" } },
    );
    response.cookies.set("travel_session", session, {
      httpOnly: true,
      secure: bridge.secure,
      sameSite: "strict",
      path: "/",
      maxAge: 7 * 86400,
    });
    return response;
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 503 });
  }
}
export const GET = forward;
export const POST = forward;
export const DELETE = forward;
