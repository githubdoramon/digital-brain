/* global __dirname, AbortController */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');
const code = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../chat/remoteTranscription.ts'), 'utf8'),
  {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  },
).outputText;
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
function setup({ status = 200, body, auth, pending, size = 1000 } = {}) {
  const calls = [];
  const deleted = [];
  let cancellations = 0;
  const fileSystem = {
    getInfoAsync: async () => ({ exists: true, isDirectory: false, size }),
    deleteAsync: async (uri) => {
      deleted.push(uri);
    },
    FileSystemUploadType: { BINARY_CONTENT: 0 },
    createUploadTask: (...args) => {
      calls.push(args);
      return {
        uploadAsync: () =>
          pending ??
          Promise.resolve({
            status,
            body:
              body ??
              JSON.stringify({
                text: 'A short draft.',
                provider: 'remote',
                engine: 'openai-compatible',
              }),
          }),
        cancelAsync: async () => {
          cancellations++;
        },
      };
    },
  };
  const exports = {};
  new Function('require', 'exports', code)((name) => {
    if (name === 'expo-file-system/legacy') return fileSystem;
    if (name === '@/api/client')
      return {
        API_BASE_URL: 'https://brain.example/api',
        getAuthRequestContext: () => auth ?? Promise.resolve({ token: 'test-token' }),
      };
    if (name === '@/chat/voiceState')
      return { normalizeTranscriptText: (text) => text.trim().replace(/\s+/gu, ' ') };
    throw new Error('Unexpected import');
  }, exports);
  return { ...exports, calls, deleted, cancellations: () => cancellations };
}
test('one authenticated binary upload returns draft text and cleans temporary audio', async () => {
  const api = setup();
  assert.deepEqual(await api.transcribeAudioFile('file:///cache/dictation.wav'), {
    text: 'A short draft.',
  });
  assert.equal(api.calls.length, 1);
  const [url, file, options] = api.calls[0];
  assert.equal(url, 'https://brain.example/api/mobile/speech/transcribe');
  assert.equal(file, 'file:///cache/dictation.wav');
  assert.equal(options.uploadType, 0);
  assert.deepEqual(options.headers, {
    Authorization: 'Bearer test-token',
    'Content-Type': 'audio/wav',
  });
  assert.deepEqual(api.deleted, [file]);
});
test('abort during authentication never starts an upload', async () => {
  const auth = deferred();
  const api = setup({ auth: auth.promise });
  const controller = new AbortController();
  const result = api.transcribeAudioFile(
    'file:///cache/dictation.wav',
    undefined,
    controller.signal,
  );
  controller.abort();
  await assert.rejects(result, (error) => error.code === 'cancelled');
  auth.resolve({ token: 'test-token' });
  assert.equal(api.calls.length, 0);
});
test('abort cancels upload and ignores a late transcript', async () => {
  const upload = deferred();
  const api = setup({ pending: upload.promise });
  const controller = new AbortController();
  const result = api.transcribeAudioFile(
    'file:///cache/dictation.wav',
    undefined,
    controller.signal,
  );
  await new Promise(setImmediate);
  controller.abort();
  await assert.rejects(result, (error) => error.code === 'cancelled');
  assert.equal(api.cancellations(), 1);
  upload.resolve({
    status: 200,
    body: JSON.stringify({ text: 'Late draft', provider: 'remote', engine: 'openai-compatible' }),
  });
});
test('provider/auth failures never leak response bodies or retry audio', async () => {
  for (const status of [401, 503]) {
    const api = setup({ status, body: 'private provider content' });
    await assert.rejects(
      api.transcribeAudioFile('file:///cache/dictation.wav'),
      (error) => !error.message.includes('private') && ['auth', 'failed'].includes(error.code),
    );
    assert.equal(api.calls.length, 1);
  }
});
test('empty transcripts and oversized recordings do not enter the draft', async () => {
  const silent = setup({
    body: JSON.stringify({ text: ' ', provider: 'remote', engine: 'openai-compatible' }),
  });
  await assert.rejects(
    silent.transcribeAudioFile('file:///cache/dictation.wav'),
    (error) => error.code === 'no_speech',
  );
  const large = setup({ size: 600 * 32000 + 4097 });
  await assert.rejects(
    large.transcribeAudioFile('file:///cache/dictation.wav'),
    (error) => error.code === 'too_long',
  );
  assert.equal(large.calls.length, 0);
});
