import { NextRequest } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "../../auth/[...nextauth]/route";
import { ProxyFetchInit } from "@/types/proxy";

const ORCHESTRATOR_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

const ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"];
const PROXY_TIMING_HEADER_PREFIX = "x-digital-brain-proxy-";

function proxyTimingHeaders(fields: {
  receivedAtMs: number;
  sessionResolutionMs: number;
  upstreamHeadersMs?: number;
  handlerToHeadersMs: number;
  headersReadyAtMs: number;
}): Record<string, string> {
  return {
    [`${PROXY_TIMING_HEADER_PREFIX}request-received-at-ms`]: String(fields.receivedAtMs),
    [`${PROXY_TIMING_HEADER_PREFIX}session-resolution-ms`]: String(fields.sessionResolutionMs),
    ...(fields.upstreamHeadersMs === undefined
      ? {}
      : { [`${PROXY_TIMING_HEADER_PREFIX}upstream-headers-ms`]: String(fields.upstreamHeadersMs) }),
    [`${PROXY_TIMING_HEADER_PREFIX}handler-to-headers-ms`]: String(fields.handlerToHeadersMs),
    [`${PROXY_TIMING_HEADER_PREFIX}headers-ready-at-ms`]: String(fields.headersReadyAtMs),
  };
}

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
  const handlerStartedAt = Date.now();
  const proxyRequestReceivedAtMs = handlerStartedAt;
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
    const headersReadyAtMs = Date.now();
    return new Response(
      JSON.stringify({
        detail: "Missing authorization header",
      }),
      {
        status: 401,
        headers: {
          "content-type": "application/json",
          ...proxyTimingHeaders({
            receivedAtMs: proxyRequestReceivedAtMs,
            sessionResolutionMs: authResolutionMs,
            handlerToHeadersMs: headersReadyAtMs - handlerStartedAt,
            headersReadyAtMs,
          }),
        },
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
    responseHeaders.delete("content-length");
    const headersReadyAtMs = Date.now();
    for (const [name, value] of Object.entries(
      proxyTimingHeaders({
        receivedAtMs: proxyRequestReceivedAtMs,
        sessionResolutionMs: authResolutionMs,
        upstreamHeadersMs: upstreamMs,
        handlerToHeadersMs: headersReadyAtMs - handlerStartedAt,
        headersReadyAtMs,
      })
    )) {
      responseHeaders.set(name, value);
    }

    const responseBody = backendResponse.body;
    if (!responseBody) {
      trace("downstream_body_completed", {
        status: backendResponse.status,
        body_bytes: 0,
        body_ms: 0,
        handler_ms: Date.now() - handlerStartedAt,
      });
      return new Response(null, {
        status: backendResponse.status,
        statusText: backendResponse.statusText,
        headers: responseHeaders,
      });
    }

    const reader = responseBody.getReader();
    const bodyStartedAt = Date.now();
    let bodyBytes = 0;
    const tracedBody = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (done) {
            controller.close();
            trace("downstream_body_completed", {
              status: backendResponse.status,
              body_bytes: bodyBytes,
              body_ms: Date.now() - bodyStartedAt,
              handler_ms: Date.now() - handlerStartedAt,
            });
            return;
          }
          bodyBytes += value.byteLength;
          controller.enqueue(value);
        } catch (error) {
          trace("downstream_body_failed", {
            status: backendResponse.status,
            body_bytes: bodyBytes,
            body_ms: Date.now() - bodyStartedAt,
            error_name: error instanceof Error ? error.name : "unknown",
          });
          controller.error(error);
        }
      },
      async cancel(reason) {
        trace("downstream_body_cancelled", {
          status: backendResponse.status,
          body_bytes: bodyBytes,
          body_ms: Date.now() - bodyStartedAt,
          reason: reason instanceof Error ? reason.name : "cancelled",
        });
        await reader.cancel(reason);
      },
    });

    return new Response(tracedBody, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    const headersReadyAtMs = Date.now();
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
        headers: {
          "content-type": "application/json",
          ...proxyTimingHeaders({
            receivedAtMs: proxyRequestReceivedAtMs,
            sessionResolutionMs: authResolutionMs,
            handlerToHeadersMs: headersReadyAtMs - handlerStartedAt,
            headersReadyAtMs,
          }),
        },
      }
    );
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE, handler as OPTIONS };
