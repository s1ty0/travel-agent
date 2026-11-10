import { type NextRequest, NextResponse } from "next/server";
import { randomBytes } from "node:crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const allowed = /^(config|tasks|tasks\/[a-f0-9-]{36})$/;
const localHosts = new Set(["127.0.0.1:3000", "localhost:3000"]);
const maxBodyBytes = 16384;

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
  if (!localHosts.has(host) || (origin && origin !== `http://${host}`)) {
    return NextResponse.json({ error: "origin_not_allowed" }, { status: 403 });
  }
  if (
    req.method === "POST" &&
    req.headers.get("content-type") !== "application/json"
  ) {
    return NextResponse.json({ error: "json_required" }, { status: 415 });
  }
  const path = (await context.params).path.join("/");
  if (!allowed.test(path) || (req.method === "POST" && path !== "tasks")) {
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
    const upstream = await fetch(`http://127.0.0.1:8000/v1/web/${path}`, {
      method: req.method,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10000),
      headers: {
        "Content-Type": "application/json",
        "X-Travel-Bridge": token,
        "X-Travel-Session": session,
      },
    });
    const data = await upstream.json();
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
      sameSite: "strict",
      path: "/",
      maxAge: 1800,
    });
    return response;
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 503 });
  }
}
export const GET = forward;
export const POST = forward;
