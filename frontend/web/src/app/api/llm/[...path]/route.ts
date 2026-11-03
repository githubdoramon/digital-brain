import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

export async function handler(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path } = await context.params;
  const target = path.join("/");
  if (!((request.method === "GET" && target === "v1/models") ||
    (request.method === "POST" && target === "v1/chat/completions"))) {
    return Response.json({ error: { message: "Unknown LLM endpoint" } }, { status: 404 });
  }
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return Response.json({ error: { message: "Missing proxy token" } }, { status: 401 });
  }
  try {
    const response = await fetch(`${BACKEND_BASE.replace(/\/$/, "")}/llm/${target}`, {
      method: request.method,
      headers: { authorization, "content-type": "application/json" },
      body: request.method === "POST" ? request.body : undefined,
      ...(request.method === "POST" ? { duplex: "half" as const } : {}),
      signal: request.signal,
      cache: "no-store",
      redirect: "manual",
    });
    const headers = new Headers({ "cache-control": "no-store", "x-accel-buffering": "no" });
    for (const name of ["content-type", "retry-after", "www-authenticate"]) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(response.body, { status: response.status, headers });
  } catch {
    return Response.json(
      { error: { message: "LLM proxy is unavailable" } },
      { status: 502, headers: { "cache-control": "no-store" } }
    );
  }
}

export { handler as GET, handler as POST };
