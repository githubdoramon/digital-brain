/* global Request, Headers, Response, URL, performance */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function load(fetch) {
  let sessionCalls = 0;
  const source = fs.readFileSync(path.join(__dirname, '../src/app/api/mobile/[...path]/route.ts'), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = { exports: {}, Headers, Response, URL, performance, Date,
    console: { info() {}, warn() {}, error() {} }, process: { env: { BACKEND_API_BASE: 'http://backend.invalid' } }, fetch,
    require(name) {
      if (name === 'next-auth') return { getServerSession: async () => { sessionCalls++; return null; } };
      if (name.includes('auth/')) return { authOptions: {} };
      throw new Error(`Unexpected dependency: ${name}`);
    },
  };
  vm.runInNewContext(code, context);
  return { handler: context.exports.handler, sessionCalls: () => sessionCalls };
}
function request(target, bearer = true) {
  const req = new Request(`https://frontend.invalid/api/mobile/${target}?vocabulary=commands-v1`, {
    method: 'POST', headers: bearer ? { authorization: 'Bearer synthetic-token' } : {}, body: 'synthetic-audio',
  });
  req.nextUrl = new URL(req.url);
  return req;
}

test('speech forwards auth, vocabulary and stream; exposes only numeric stage timing', async () => {
  let calls = 0;
  const req = request('glasses/speech-eval/transcribe');
  const route = load(async (url, options) => {
    calls++;
    assert.equal(url.searchParams.get('vocabulary'), 'commands-v1');
    assert.equal(options.headers.get('authorization'), 'Bearer synthetic-token');
    assert.equal(options.body, req.body);
    return new Response('{"text":"synthetic"}', { headers: {
      'server-timing': 'speech_auth;dur=2, speech_backend;dur=20', 'content-type': 'application/json',
    } });
  });
  const response = await route.handler(req, { params: Promise.resolve({ path: ['glasses', 'speech-eval', 'transcribe'] }) });
  assert.equal(calls, 1);
  assert.equal(route.sessionCalls(), 0);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const timing = response.headers.get('server-timing');
  assert.match(timing, /speech_auth;dur=2/);
  assert.match(timing, /speech_proxy_backend;dur=\d/);
  assert.match(timing, /speech_proxy_auth;dur=\d/);
  assert.equal(timing.includes('synthetic'), false);
});

test('missing authentication never forwards audio', async () => {
  const route = load(async () => { throw new Error('Must not forward'); });
  const response = await route.handler(request('speech/transcribe', false), { params: Promise.resolve({ path: ['speech', 'transcribe'] }) });
  assert.equal(response.status, 401);
  assert.equal(route.sessionCalls(), 1);
});

test('backend rejection is returned without retry', async () => {
  let calls = 0;
  const route = load(async () => { calls++; return new Response('{}', { status: 401 }); });
  const response = await route.handler(request('speech/transcribe'), { params: Promise.resolve({ path: ['speech', 'transcribe'] }) });
  assert.equal(response.status, 401);
  assert.equal(calls, 1);
});

for (const target of ['glasses/chat', 'glasses/chat/speech-test']) {
test(`${target} carries cancellation, authentication and ephemeral audio without retries`, async () => {
  const req = request(target);
  let calls = 0;
  const route = load(async (url, options) => {
    calls++;
    assert.equal(url.pathname, `/mobile/${target}`);
    assert.equal(options.signal, req.signal);
    assert.equal(options.headers.get('authorization'), 'Bearer synthetic-token');
    return new Response('{"audio":{"encoding":"pcm16"}}', { headers: { 'content-type': 'application/json' } });
  });
  const response = await route.handler(req, { params: Promise.resolve({ path: target.split('/') }) });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(calls, 1);
});
}
