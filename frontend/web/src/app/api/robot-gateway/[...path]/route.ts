import { NextRequest } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "../../auth/[...nextauth]/route";
import { ProxyFetchInit } from "@/types/proxy";

const ROBOT_GATEWAY_BASE =
  process.env.ROBOT_GATEWAY_API_BASE ?? "http://localhost:8001";

const SERVICE_API_KEY = process.env.ORCHESTRATOR_API_KEY ?? "";

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
  const url = new URL(`${ROBOT_GATEWAY_BASE}/${targetPath}`);

  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.append(key, value);
  });

  const headers = new Headers(request.headers);
  headers.delete("host");

  const authHeader = await buildAuthorizationHeader(request);
  if (authHeader) {
    headers.set("authorization", authHeader);
  } else {
    headers.delete("authorization");
  }

  // Inject service API key for backend authentication
  if (SERVICE_API_KEY) {
    headers.set("x-service-api-key", SERVICE_API_KEY);
  }

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

    return new Response(backendResponse.body, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("Robot gateway proxy error", error);
    return new Response(
      JSON.stringify({
        detail: "Failed to reach robot-gateway service",
      }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      }
    );
  }
}

export {
  handler as GET,
  handler as POST,
  handler as PUT,
  handler as PATCH,
  handler as DELETE,
  handler as OPTIONS,
};
