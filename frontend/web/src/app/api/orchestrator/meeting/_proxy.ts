import { NextRequest } from "next/server";
import { getServerSession } from "next-auth";

import { authOptions } from "../../auth/[...nextauth]/route";
import { ProxyFetchInit } from "@/types/proxy";

const ORCHESTRATOR_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

async function getAuthorizationHeader(request: NextRequest): Promise<string | undefined> {
  const existing = request.headers.get("authorization");
  if (existing) {
    return existing;
  }

  const session = await getServerSession(authOptions);
  const idToken = session?.idToken;
  return idToken ? `Bearer ${idToken}` : undefined;
}

export async function proxyMeetingRequest(request: NextRequest, backendPath: string): Promise<Response> {
  if (request.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  const url = `${ORCHESTRATOR_BASE}${backendPath}`;
  const headers = new Headers(request.headers);
  headers.delete("host");

  const authHeader = await getAuthorizationHeader(request);
  if (authHeader) {
    headers.set("authorization", authHeader);
  } else {
    headers.delete("authorization");
  }

  const bodyBuffer = await request.arrayBuffer();
  const init: ProxyFetchInit = {
    method: "POST",
    headers,
    body: bodyBuffer.byteLength ? bodyBuffer : undefined,
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
    console.error("Meeting proxy error", error);
    return new Response(
      JSON.stringify({ detail: "Failed to reach orchestrator service" }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      }
    );
  }
}

