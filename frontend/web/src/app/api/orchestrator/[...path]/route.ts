import { NextRequest } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "../../auth/[...nextauth]/route";
import { ProxyFetchInit } from "@/types/proxy";

const ORCHESTRATOR_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

const ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"];

async function buildAuthorizationHeader(request: NextRequest): Promise<{
  header?: string;
  source: "request" | "session" | "missing";
}> {
  const existing = request.headers.get("authorization");
  if (existing) {
    return { header: existing, source: "request" };
  }

  const session = await getServerSession(authOptions);
  const idToken = session?.idToken;
  return idToken
    ? { header: `Bearer ${idToken}`, source: "session" }
    : { source: "missing" };
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
  const url = new URL(`${ORCHESTRATOR_BASE}/${targetPath}`);

  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.append(key, value);
  });

  const headers = new Headers(request.headers);
  headers.delete("host");

  const auth = await buildAuthorizationHeader(request);
  if (auth.header) {
    headers.set("authorization", auth.header);
  } else {
    headers.delete("authorization");
  }

  console.info("[orchestrator-proxy] request", {
    method: request.method,
    path: targetPath,
    url: url.toString(),
    auth_source: auth.source,
    has_authorization: !!auth.header,
  });

  const init: ProxyFetchInit = {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    duplex: "half",
  };

  try {
    const backendResponse = await fetch(url, init);
    const responseHeaders = new Headers(backendResponse.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("transfer-encoding");

    console.info("[orchestrator-proxy] response", {
      method: request.method,
      path: targetPath,
      status: backendResponse.status,
      ok: backendResponse.ok,
    });

    return new Response(backendResponse.body, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("Orchestrator proxy error", error);
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
