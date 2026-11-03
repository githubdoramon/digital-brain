import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));

function load(sourcePath, fetch) {
  const source = readFileSync(path.join(testDirectory, "..", sourcePath), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(output, {
    exports, fetch, Headers, Response,
    process: { env: { BACKEND_API_BASE: "http://backend.example:8000/" } },
    require(name) {
      if (name === "next-auth/middleware") {
        return { withAuth: () => () => Response.json({ sessionAuth: true }, { status: 401 }) };
      }
      if (name === "next/server") {
        return { NextResponse: {
          json: Response.json,
          next: () => Response.json({ next: true }),
          rewrite: (url) => Response.json({ rewrite: url.pathname }),
        } };
      }
      throw new Error(`Unexpected dependency: ${name}`);
    },
  });
  return exports;
}

const routePath = "src/app/api/llm/[...path]/route.ts";
const context = (path) => ({ params: Promise.resolve({ path }) });

test("model discovery forwards only the client bearer token, without session login", async () => {
  let calls = 0;
  const { GET } = load(routePath, async (url, init) => {
    calls++;
    assert.equal(url, "http://backend.example:8000/llm/v1/models");
    assert.deepEqual(Object.keys(init.headers).sort(), ["authorization", "content-type"]);
    assert.equal(init.headers.authorization, "Bearer dedicated-token");
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "manual");
    return Response.json({ data: [{ id: "chat-fast" }] });
  });
  const response = await GET(new Request("http://frontend.example/llm/v1/models", {
    headers: { authorization: "Bearer dedicated-token", cookie: "session=private" },
  }), context(["v1", "models"]));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { data: [{ id: "chat-fast" }] });
  assert.equal(calls, 1);
});

test("SSE is returned before completion, and cancellation reaches the upstream body", async () => {
  let cancelled = false;
  const encoder = new TextEncoder();
  const { POST } = load(routePath, async (_, init) => {
    assert.equal(init.signal.aborted, false);
    assert.equal(await new Response(init.body).text(), '{"model":"chat-fast"}');
    return new Response(new ReadableStream({
      start(controller) { controller.enqueue(encoder.encode("data: first\n\n")); },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "text/event-stream", "content-length": "999", "content-encoding": "gzip" } });
  });
  const response = await POST(new Request("http://frontend.example/llm/v1/chat/completions", {
    method: "POST", body: '{"model":"chat-fast"}', headers: { authorization: "Bearer token" },
  }), context(["v1", "chat", "completions"]));
  assert.equal(response.headers.get("content-length"), null);
  assert.equal(response.headers.get("content-encoding"), null);
  const reader = response.body.getReader();
  assert.equal(new TextDecoder().decode((await reader.read()).value), "data: first\n\n");
  await reader.cancel();
  assert.equal(cancelled, true);
});

test("invalid paths and missing tokens cannot reach the backend", async () => {
  const { GET, POST } = load(routePath, () => { throw new Error("Must not fetch"); });
  assert.equal((await GET(new Request("http://frontend.example"), context(["v1", "models"]))).status, 401);
  assert.equal((await POST(new Request("http://frontend.example", { method: "POST" }), context(["v1", "models"]))).status, 404);
  assert.equal((await GET(new Request("http://frontend.example"), context(["..", "system"]))).status, 404);
});

test("backend authentication/rate errors survive and network errors are sanitized", async () => {
  const request = new Request("http://frontend.example", { headers: { authorization: "Bearer token" } });
  for (const status of [401, 429, 503]) {
    const { GET } = load(routePath, async () => Response.json({ detail: "Unavailable" }, {
      status, headers: { "retry-after": "5", "www-authenticate": "Bearer" },
    }));
    const response = await GET(request, context(["v1", "models"]));
    assert.equal(response.status, status);
    assert.equal(response.headers.get("retry-after"), "5");
    assert.equal(response.headers.get("www-authenticate"), "Bearer");
  }
  const { GET } = load(routePath, () => { throw new Error("private backend address"); });
  const response = await GET(request, context(["v1", "models"]));
  assert.equal(response.status, 502);
  assert.equal((await response.text()).includes("private"), false);
});

test("middleware rewrites public LLM routes and leaves token validation to the backend", async () => {
  const middleware = load("src/middleware.ts").default;
  function request(pathname, token) {
    return { nextUrl: { pathname, clone: () => new URL(`http://frontend.example${pathname}`) },
      headers: new Headers(token ? { authorization: "Bearer token" } : {}) };
  }
  assert.equal(middleware(request("/llm/v1/models", false)).status, 401);
  assert.deepEqual(await middleware(request("/llm/v1/models", true)).json(), { rewrite: "/api/llm/v1/models" });
  assert.deepEqual(await middleware(request("/api/llm/v1/models", true)).json(), { next: true });
  assert.deepEqual(await middleware(request("/api/orchestrator/contacts", false)).json(), { sessionAuth: true });
});
