const ORCHESTRATOR_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

const allowedMethods = new Set(["POST", "OPTIONS"]);

async function proxyToBackend(request: Request): Promise<Response> {
  if (!allowedMethods.has(request.method)) {
    return new Response("Method Not Allowed", { status: 405 });
  }

  const url = `${ORCHESTRATOR_BASE}/webhooks/telegram/messages`;
  const headers = new Headers(request.headers);
  headers.delete("host");

  const body = request.method === "POST" ? await request.arrayBuffer() : undefined;

  try {
    const response = await fetch(url, {
      method: request.method,
      headers,
      body,
      duplex: "half",
    });

    const passthroughHeaders = new Headers(response.headers);
    passthroughHeaders.delete("content-encoding");
    passthroughHeaders.delete("transfer-encoding");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: passthroughHeaders,
    });
  } catch (error) {
    console.error("Telegram webhook proxy error", error);
    return new Response(
      JSON.stringify({ detail: "Failed to reach orchestrator service" }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      }
    );
  }
}

export const POST = proxyToBackend;
export const OPTIONS = proxyToBackend;
