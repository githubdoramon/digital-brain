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

  const proxyStartedAt = performance.now();
  const { path = [] } = await context.params;
  const targetPath = path.join("/");
  const url = new URL(`${ORCHESTRATOR_BASE}/mobile/${targetPath}`);

  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.append(key, value);
  });

  const headers = new Headers(request.headers);
  headers.delete("host");
  const isLocationUpdate = request.method === "POST" && targetPath === "location";
  const locationRequestId = isLocationUpdate
    ? headers.get("x-location-debug-request-id") ?? "none"
    : null;
  const startedAt = Date.now();

  const isSpeechRequest = request.method === "POST" && new Set([
    "glasses/transcribe", "glasses/speech-eval/prepare", "glasses/speech-eval/transcribe", "speech/transcribe",
  ]).has(targetPath);
  const isGlassesChat = request.method === "POST" && targetPath === "glasses/chat";
  const authStartedAt = performance.now();
  const authHeader = await buildAuthorizationHeader(request);
  const proxyAuthMs = performance.now() - authStartedAt;
  if (authHeader) {
    headers.set("authorization", authHeader);
  } else {
    console.warn("[mobile proxy] missing authorization header", {
      path: `/${targetPath}`,
      method: request.method,
      ...(isLocationUpdate ? { debug_request_id: locationRequestId } : {}),
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
    ...(isGlassesChat ? { signal: request.signal } : {}),
  };

  try {
    if (isLocationUpdate) {
      console.info("[mobile location proxy] request", {
        debug_request_id: locationRequestId,
        method: request.method,
      });
    }
    const backendStartedAt = performance.now();
    const backendResponse = await fetch(url, init);
    const proxyBackendMs = performance.now() - backendStartedAt;
    if (isLocationUpdate) {
      console.info("[mobile location proxy] response", {
        debug_request_id: locationRequestId,
        status: backendResponse.status,
        duration_ms: Date.now() - startedAt,
      });
    }
    if (backendResponse.status === 401) {
      console.warn("[mobile proxy] backend returned 401", {
        path: `/${targetPath}`,
        method: request.method,
        ...(isLocationUpdate ? { debug_request_id: locationRequestId } : {}),
      });
    }
    const responseHeaders = new Headers(backendResponse.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("transfer-encoding");
    responseHeaders.delete("content-length");
    if (isSpeechRequest) {
      const backendTiming = responseHeaders.get("server-timing");
      const timing = [backendTiming, `speech_proxy_auth;dur=${proxyAuthMs.toFixed(2)}`,
        `speech_proxy_backend;dur=${proxyBackendMs.toFixed(2)}`,
        `speech_proxy;dur=${(performance.now() - proxyStartedAt).toFixed(2)}`].filter(Boolean).join(", ");
      responseHeaders.set("server-timing", timing);
      responseHeaders.set("cache-control", "no-store");
    }
    if (isGlassesChat) responseHeaders.set("cache-control", "no-store");
    return new Response(backendResponse.body, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    if (isLocationUpdate) {
      console.error("[mobile location proxy] failed", {
        debug_request_id: locationRequestId,
        error_type: error instanceof Error ? error.name : typeof error,
        duration_ms: Date.now() - startedAt,
      });
    }
    if (isGlassesChat) {
      console.error("[mobile glasses chat proxy] request failed");
    } else if (!isLocationUpdate) {
      console.error("Orchestrator mobile proxy error", error);
    }
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
