/* Focused, dependency-injected smoke tests for the native-backed command path.
 * The TypeScript modules are transpiled in memory so this stays runnable in the
 * repository without adding a second test runner to the Expo app.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');
const originalLoad = Module._load;
const originalExtension = Module._extensions['.ts'];
const originalSetTimeout = global.setTimeout;

const deletedUris = [];
const led = { orange: 0, red: 0 };
const nativeCalls = { play: [], stop: [] };
const listeners = new Map();
const session = { threadId: 'old-thread', pendingEventId: 'pending-event' };
let apiResponse;
let apiCall;
let downloadCall;
let deadlineCallback;
const whisperFailureContext = {
  transcribeData: () => ({ promise: Promise.reject(new Error('native model unavailable')) }),
};

const native = {
  addListener(event, callback) {
    listeners.set(event, callback);
    return { remove: () => listeners.delete(event) };
  },
  playSpeechAudio(commandId, uri) {
    nativeCalls.play.push({ commandId, uri });
    return Promise.resolve({ started: true });
  },
  stopSpeechAudio(commandId) {
    nativeCalls.stop.push(commandId);
    return Promise.resolve({ stopped: true });
  },
};

const fileSystem = {
  cacheDirectory: 'file:///cache/',
  documentDirectory: 'file:///documents/',
  deleteAsync: async (uri) => {
    deletedUris.push(uri);
  },
  downloadAsync: async (url, destination) => {
    if (downloadCall) return downloadCall(url, destination);
    return { status: 200, uri: destination };
  },
  getInfoAsync: async () => ({ exists: true, size: 128 }),
};

const api = {
  API_BASE_URL: 'https://brain.invalid',
  apiFetch: async (...args) => {
    apiCall?.(...args);
    return typeof apiResponse === 'function' ? apiResponse(...args) : apiResponse;
  },
  getAuthRequestContext: async () => ({ token: 'test-token', authDiagnostics: {} }),
};

function transpile(filename) {
  const source = fs.readFileSync(filename, 'utf8');
  return ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
      sourceMap: false,
    },
    fileName: filename,
  }).outputText;
}

Module._extensions['.ts'] = (module, filename) => module._compile(transpile(filename), filename);
Module._load = function patchedLoad(request, parent, isMain) {
  const mocks = {
    'expo-file-system/legacy': { __esModule: true, ...fileSystem },
    '@/api/client': api,
    '@/chat/localTranscription': {
      LOCAL_WHISPER_MODEL_FILE_NAME: 'ggml-small.en.bin',
      warmEnglishWhisperContext: async () => whisperFailureContext,
      invalidateEnglishWhisperContext: () => undefined,
      isMissingNativeWhisperContextError: () => false,
    },
    '@/chat/voiceState': { normalizeTranscriptText: (value) => String(value).trim() },
    '@/location/clientContext': { getClientContext: () => ({ timezone: 'UTC', locale: 'en-US' }) },
    '@/chat/session': {
      loadChatSession: async () => ({ ...session }),
      saveChatSession: async (next) => Object.assign(session, next),
    },
    '@/modules/digital-brain-glasses-alerts/src': { __esModule: true, default: native },
    './debug': {
      appendMentraDebugLog: async () => undefined,
      appendWakeCommandDebugLog: async () => undefined,
    },
    './sdk': {
      blinkMentraOrangeLed: async () => {
        led.orange += 1;
        return 'orange';
      },
      blinkMentraRedLed: async () => {
        led.red += 1;
        return 'red';
      },
    },
  };
  if (Object.prototype.hasOwnProperty.call(mocks, request)) return mocks[request];
  return originalLoad.call(this, request, parent, isMain);
};

const registry = require(path.join(mobileRoot, 'mentraCapture', 'commandRegistry.ts'));
const agent = require(path.join(mobileRoot, 'mentraCapture', 'glassesCommandAgent.ts'));
const transcription = require(path.join(mobileRoot, 'mentraCapture', 'commandTranscription.ts'));
const transcript = (id = '11111111-1111-4111-8111-111111111111') => ({
  commandId: id,
  transcript: 'what is next',
  rawTranscript: 'what is next',
  language: 'en',
  audioDurationMs: 800,
  wakeDetectedAt: Date.now(),
});
const hooks = {
  pauseListening: async () => undefined,
  resumeListening: async () => {
    hooks.resumed += 1;
  },
  resumed: 0,
};

function reset() {
  registry.clearDeviceCommandInterceptorsForTests();
  agent.resetGlassesCommandAgentForTests();
  apiResponse = undefined;
  apiCall = undefined;
  downloadCall = undefined;
  deadlineCallback = undefined;
  deletedUris.splice(0);
  nativeCalls.play.splice(0);
  nativeCalls.stop.splice(0);
  listeners.clear();
  led.orange = 0;
  led.red = 0;
  hooks.resumed = 0;
  Object.assign(session, { threadId: 'old-thread', pendingEventId: 'pending-event' });
}

async function tick() {
  await new Promise((resolve) => originalSetTimeout(resolve, 0));
}

async function testRegistry() {
  reset();
  assert.equal(registry.getDeviceCommandInterceptors().length, 0);
  const calls = [];
  const removeFirst = registry.registerDeviceCommandInterceptor({
    id: 'first',
    matches: () => true,
    execute: async () => {
      calls.push('first');
      return { outcome: 'control_completed' };
    },
  });
  registry.registerDeviceCommandInterceptor({
    id: 'second',
    matches: () => true,
    execute: async () => {
      calls.push('second');
      return { outcome: 'control_completed' };
    },
  });
  const result = await registry.interceptDeviceCommand({
    commandId: 'id',
    transcript: 'slash new',
  });
  assert.equal(result.outcome, 'control_completed');
  assert.deepEqual(calls, ['first']);
  removeFirst();
  assert.equal(
    (await registry.interceptDeviceCommand({ commandId: 'id', transcript: 'slash new' })).outcome,
    'control_completed',
  );
  assert.deepEqual(calls, ['first', 'second']);
}

async function testOutcomesAndSession() {
  reset();
  apiResponse = {
    outcome: 'shortcut_completed',
    command_id: transcript().commandId,
    thread_id: 'new-thread',
    answer: 'New session started.',
    silent: true,
  };
  await agent.dispatchGlassesCommand(transcript(), hooks);
  assert.equal(session.threadId, 'new-thread');
  assert.equal(session.pendingEventId, null);
  assert.equal(led.orange, 1);
  assert.equal(led.red, 0);
  assert.equal(hooks.resumed, 1);
}

async function testAudioCompletionAndCleanup() {
  reset();
  const id = '22222222-2222-4222-8222-222222222222';
  apiResponse = {
    outcome: 'agent_response',
    command_id: id,
    thread_id: 'old-thread',
    audio: { download_url: '/mobile/glasses/audio/a' },
  };
  const run = agent.dispatchGlassesCommand(transcript(id), hooks);
  await tick();
  listeners.get('onSpeechPlaybackFinished')({ commandId: id, status: 'completed', durationMs: 20 });
  await run;
  assert.equal(nativeCalls.play.length, 1);
  assert.equal(led.orange, 1);
  assert.equal(led.red, 0);
  assert.ok(deletedUris.length >= 2);
  assert.ok(
    deletedUris.every(
      (uri) => uri === 'file:///cache/glasses-command-22222222-2222-4222-8222-222222222222.audio',
    ),
  );
}

async function testErrorOutcome() {
  reset();
  const id = '66666666-6666-4666-8666-666666666666';
  apiResponse = {
    outcome: 'error',
    command_id: id,
    error: { code: 'ha_unavailable', message: 'Unavailable.' },
  };
  await agent.dispatchGlassesCommand(transcript(id), hooks);
  assert.equal(led.orange, 0);
  assert.equal(led.red, 1);
  assert.equal(hooks.resumed, 1);
}

async function testTranscriptionFailureCallback() {
  reset();
  const originalNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  const failures = [];
  try {
    transcription.startGlassesCommandTranscription(
      0,
      () => undefined,
      [],
      {
        wakePhrase: 'hey brain',
        detectionAudioTimeMs: 0,
        preRollStartAudioTimeMs: 0,
        preRollEndAudioTimeMs: 0,
      },
      () => undefined,
      (event) => failures.push(event),
    );
    const speech = new Int16Array(1600).fill(10000);
    now = 1400;
    transcription.acceptGlassesCommandPcm(
      speech,
      () => undefined,
      () => undefined,
      (event) => failures.push(event),
    );
    now = 1550;
    transcription.acceptGlassesCommandPcm(
      speech,
      () => undefined,
      () => undefined,
      (event) => failures.push(event),
    );
    now = 4200;
    transcription.acceptGlassesCommandPcm(
      new Int16Array(1600),
      () => undefined,
      () => undefined,
      (event) => failures.push(event),
    );
    await tick();
    await tick();
    assert.equal(failures.length, 1);
    assert.equal(failures[0].error, 'native model unavailable');
  } finally {
    transcription.cancelGlassesCommandTranscription('test_cleanup');
    Date.now = originalNow;
  }
}

async function testSingleFlightAndStableId() {
  reset();
  let resolveRequest;
  const requestIds = [];
  apiCall = (_path, options) => requestIds.push(JSON.parse(options.body).command_id);
  apiResponse = () =>
    new Promise((resolve) => {
      resolveRequest = resolve;
    });
  const id = '33333333-3333-4333-8333-333333333333';
  const first = agent.dispatchGlassesCommand(transcript(id), hooks);
  const second = agent.dispatchGlassesCommand(transcript(id), hooks);
  await tick();
  assert.equal(agent.getGlassesCommandInFlight().commandId, id);
  assert.equal(agent.getGlassesCommandInFlight().state, 'executing');
  resolveRequest({ outcome: 'control_completed', command_id: id });
  await Promise.all([first, second]);
  assert.equal(agent.getGlassesCommandInFlight(), null);
  apiResponse = { outcome: 'control_completed', command_id: id };
  await agent.dispatchGlassesCommand(transcript(id), hooks);
  assert.deepEqual(requestIds, [id, id]);
}

async function testTimeoutDuringRequestAndLateResponse() {
  reset();
  let resolveRequest;
  apiResponse = () =>
    new Promise((resolve) => {
      resolveRequest = resolve;
    });
  global.setTimeout = (callback, ms, ...args) =>
    ms === 70000
      ? ((deadlineCallback = callback), { unref() {} })
      : originalSetTimeout(callback, ms, ...args);
  const id = '44444444-4444-4444-8444-444444444444';
  const run = agent.dispatchGlassesCommand(transcript(id), hooks);
  await tick();
  deadlineCallback();
  await run;
  assert.equal(led.red, 1);
  assert.equal(hooks.resumed, 1);
  resolveRequest({ outcome: 'agent_response', command_id: id, audio: { download_url: '/late' } });
  await tick();
  assert.equal(nativeCalls.play.length, 0);
  global.setTimeout = originalSetTimeout;
}

async function testTimeoutDuringDownloadAndPlayback() {
  reset();
  global.setTimeout = (callback, ms, ...args) =>
    ms === 70000
      ? ((deadlineCallback = callback), { unref() {} })
      : originalSetTimeout(callback, ms, ...args);
  const id = '55555555-5555-4555-8555-555555555555';
  apiResponse = { outcome: 'agent_response', command_id: id, audio: { download_url: '/audio' } };
  let resolveDownload;
  downloadCall = async (url, destination) =>
    new Promise((resolve) => {
      resolveDownload = () => resolve({ status: 200, uri: destination });
    });
  const downloadRun = agent.dispatchGlassesCommand(transcript(id), hooks);
  while (!resolveDownload) await tick();
  deadlineCallback();
  await downloadRun;
  resolveDownload();
  await tick();
  assert.equal(nativeCalls.play.length, 0);
  assert.ok(deletedUris.length >= 1);

  reset();
  apiResponse = { outcome: 'agent_response', command_id: id, audio: { download_url: '/audio' } };
  const playbackRun = agent.dispatchGlassesCommand(transcript(id), hooks);
  while (nativeCalls.play.length === 0) await tick();
  assert.equal(nativeCalls.play.length, 1);
  deadlineCallback();
  await playbackRun;
  assert.equal(nativeCalls.stop.length, 1);
  assert.equal(led.red, 1);
  global.setTimeout = originalSetTimeout;
}

(async () => {
  try {
    await testRegistry();
    await testOutcomesAndSession();
    await testAudioCompletionAndCleanup();
    await testErrorOutcome();
    await testTranscriptionFailureCallback();
    await testSingleFlightAndStableId();
    await testTimeoutDuringRequestAndLateResponse();
    await testTimeoutDuringDownloadAndPlayback();
    console.log('glasses command registry/coordinator tests: PASS');
  } finally {
    global.setTimeout = originalSetTimeout;
    Module._load = originalLoad;
    Module._extensions['.ts'] = originalExtension;
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
