import { NextRequest } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "../../auth/[...nextauth]/route";
import { ProxyFetchInit } from "@/types/proxy";

const ORCHESTRATOR_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

const ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"];

async function buildAuthorizationHeader(request: NextRequest): Promise<string | undefined> {
  const existing = request.headers.get("authorization");
  if (existing) {
    return existing;
  }

  const session = await getServerSession(authOptions);
  const idToken = session?.idToken;
  return idToken ? `Bearer ${idToken}` : undefined;
}

export async function handler(
  request: NextRequest,
  context: { params: Promise<{ path?: string[] }> }
) {
  if (!ALLOWED_METHODS.includes(request.method)) {
    return new Response("Method Not Allowed", { status: 405 });
  }

  const { path = [] } = await context.params;
  const targetPath = path.join("/");
  const commandId = request.headers.get("x-glasses-command-id");
  const traceGlassesRequest = Boolean(commandId) && targetPath.startsWith("glasses/");
  const tracePath =
    targetPath === "glasses/commands"
      ? "/glasses/commands"
      : targetPath.startsWith("glasses/audio/")
        ? "/glasses/audio/[audio_id]"
        : `/${targetPath}`;
  const handlerStartedAt = Date.now();
  const trace = (event: string, fields: Record<string, unknown> = {}) => {
    if (!traceGlassesRequest) return;
    console.info(`[mobile-proxy] ${event}`, {
      command_id: commandId,
      method: request.method,
      path: tracePath,
      ...fields,
    });
  };
  trace("request_received");
  const url = new URL(`${ORCHESTRATOR_BASE}/mobile/${targetPath}`);

  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.append(key, value);
  });

  const headers = new Headers(request.headers);
  headers.delete("host");

  const authStartedAt = Date.now();
  const authHeader = await buildAuthorizationHeader(request);
  const authResolutionMs = Date.now() - authStartedAt;
  if (authHeader) {
    headers.set("authorization", authHeader);
  } else {
    trace("request_rejected", {
      status: 401,
      auth_resolution_ms: authResolutionMs,
      handler_ms: Date.now() - handlerStartedAt,
      reason: "missing_authorization",
    });
    console.warn("[mobile proxy] missing authorization header", {
      path: `/${targetPath}`,
      method: request.method,
    });
    return new Response(
      JSON.stringify({
        detail: "Missing authorization header",
      }),
      {
        status: 401,
        headers: { "content-type": "application/json" },
      }
    );
  }

  const init: ProxyFetchInit = {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    duplex: "half",
  };

  const upstreamStartedAt = Date.now();
  try {
    const backendResponse = await fetch(url, init);
    const upstreamMs = Date.now() - upstreamStartedAt;
    trace("upstream_headers_received", {
      status: backendResponse.status,
      auth_resolution_ms: authResolutionMs,
      upstream_ms: upstreamMs,
      handler_to_headers_ms: Date.now() - handlerStartedAt,
    });
    if (backendResponse.status === 401) {
      console.warn("[mobile proxy] backend returned 401", {
        path: `/${targetPath}`,
        method: request.method,
      });
    }
    const responseHeaders = new Headers(backendResponse.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("transfer-encoding");

    return new Response(backendResponse.body, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    trace("upstream_failed", {
      auth_resolution_ms: authResolutionMs,
      upstream_ms: Date.now() - upstreamStartedAt,
      handler_to_failure_ms: Date.now() - handlerStartedAt,
      error_name: error instanceof Error ? error.name : "unknown",
    });
    console.error("Orchestrator mobile proxy error", error);
    return new Response(
      JSON.stringify({
        detail: "Failed to reach orchestrator service",
      }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      }
    );
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE, handler as OPTIONS };
