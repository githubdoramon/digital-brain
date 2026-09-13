const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
function load(file, mocks, globals = {}) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  vm.runInNewContext(
    code,
    {
      module,
      exports: module.exports,
      console,
      Date,
      setTimeout,
      clearTimeout,
      AbortController,
      require(name) {
        if (name in mocks) return mocks[name];
        throw new Error(`Unexpected import: ${name}`);
      },
      ...globals,
    },
    { filename: file },
  );
  return module.exports;
}
const debug = { reportLocationDebugEvent() {} };
const runtime = {
  getLocationRuntimeState: () => ({ appState: 'background', lastAppStateChangeAt: null }),
};
const sample = {
  id: 'native-1',
  latitude: 12,
  longitude: 34,
  timestamp: 1_700_000_000_000,
  accuracy: 20,
  timezone: 'UTC',
};
async function handoff() {
  const stored = new Map();
  let persistFails = true,
    ackFails = false,
    ackCount = 0,
    reads = 0;
  const native = {
    readRuntimeLocations: async () => {
      reads++;
      return [sample];
    },
    acknowledgeRuntimeLocations: async (ids) => {
      assert.deepEqual(Array.from(ids), ['native-1']);
      ackCount++;
      if (ackFails) throw Error('ack failure');
    },
  };
  const location = load('location/foregroundLocation.ts', {
    'react-native': { Platform: { OS: 'android' } },
    '@/modules/digital-brain-glasses-alerts/src': native,
    './backgroundLocationQueue': {
      enqueueBackgroundLocationEntry: async (entry) => {
        if (persistFails) throw Error('disk failure');
        stored.set(entry.id, entry);
      },
    },
    './debugState': debug,
    './runtimeState': runtime,
  });
  await assert.rejects(location.transferNativeLocations(), /disk failure/);
  assert.equal(ackCount, 0, 'Native copy survives failed JS persistence');
  persistFails = false;
  ackFails = true;
  await assert.rejects(location.transferNativeLocations(), /ack failure/);
  ackFails = false;
  const before = reads;
  await Promise.all([location.transferNativeLocations(), location.transferNativeLocations()]);
  assert.equal(reads - before, 1, 'Concurrent handoffs join one transfer');
  assert.equal(stored.size, 1, 'Replay after lost ACK retains stable identity');
  assert.equal([...stored.values()][0].timezone, 'UTC', 'Capture timezone survives delivery delay');
  assert.equal([...stored.values()][0].source, 'android_foreground_location');
}
async function draining() {
  let disk = '[]',
    posts = 0,
    now = 0,
    fail = false;
  class Clock extends Date {
    static now() {
      return now;
    }
  }
  const storage = {
    getItem: async () => disk,
    setItem: async (_key, value) => {
      disk = value;
    },
  };
  const queue = load(
    'location/backgroundLocationQueue.ts',
    {
      '@react-native-async-storage/async-storage': storage,
      '@/api/client': {
        API_BASE_URL: 'https://api.example.com',
        apiFetch: async () => {
          posts++;
          now += 15_000;
          if (fail) throw Error('offline');
          return {};
        },
      },
      '@/auth/backgroundToken': {
        getStoredGoogleIdToken: async () => 'fake-token',
        getStoredGoogleIdTokenDiagnostics: async () => ({}),
        refreshStoredGoogleIdToken: async () => 'fake-token',
      },
      '@/location/debugState': debug,
      '@/location/runtimeState': runtime,
    },
    { Date: Clock },
  );
  for (let i = 0; i < 8; i++)
    await queue.enqueueBackgroundLocationEntry({
      id: `sample-${i}`,
      lat: 12,
      lon: 34,
      capturedAtMs: 1_700_000_000_000 + i * 300_000,
      capturedAt: new Date(1_700_000_000_000 + i * 300_000).toISOString(),
      enqueuedAt: new Date().toISOString(),
      source: 'android_foreground_location',
      batchId: 'fake-batch',
      sampleIndex: i + 1,
      sampleCount: 8,
      executionContext: 'background',
      sampleAgeSeconds: 0,
      isBufferedFlush: false,
      attemptCount: 0,
    });
  await Promise.all([
    queue.drainQueuedBackgroundLocations('foreground_service'),
    queue.drainQueuedBackgroundLocations('background_task_worker'),
  ]);
  assert.equal(posts, 3, 'Concurrent worker triggers share one 45-second budget');
  assert.equal(JSON.parse(disk).length, 5, 'Budget exhaustion preserves remaining samples');
  fail = true;
  await queue.drainQueuedBackgroundLocations('foreground_service');
  assert.equal(JSON.parse(disk).length, 5, 'Offline upload preserves queue');
  fail = false;
  await queue.drainQueuedBackgroundLocations('foreground_service');
  assert.equal(JSON.parse(disk).length, 2, 'Later runtime opportunity resumes delivery');
}
async function work() {
  const events = [];
  const diagnostics = [];
  let healthFails = false;
  let locationEnabled = true;
  let glassesEnabled = false;
  let bluetoothGranted = false;
  let transferFails = false;
  let afterUpload = () => {};
  let afterPermissionCheck = () => {};
  const mod = load('runtime/backgroundRuntime.ts', {
    'react-native': {
      AppRegistry: { registerHeadlessTask: (name) => events.push(name) },
      Platform: { OS: 'android', Version: 36 },
      PermissionsAndroid: {
        PERMISSIONS: {},
        check: async () => {
          afterPermissionCheck();
          return bluetoothGranted;
        },
      },
    },
    '@/mentraCapture/sdk': { ensureMentraConnection: async () => events.push('connect') },
    '@/mentraCapture/maintenance': { assertGlassesNotUpdating: async () => {} },
    '@/modules/digital-brain-glasses-alerts/src': {
      getImageEnhancementDeviceHealth: async () => {
        if (healthFails) throw Error('health unavailable');
        return { batteryPercent: 72, charging: false, thermalStatus: 0 };
      },
      getAppRuntimeStatus: async () => ({
        owners: [locationEnabled && 'location', glassesEnabled && 'glasses'].filter(Boolean),
      }),
    },
    '@/location/foregroundLocation': {
      transferNativeLocations: async () => {
        events.push('persist');
        if (transferFails) throw Error('storage unavailable');
      },
    },
    '@/location/backgroundLocationQueue': {
      drainQueuedBackgroundLocations: async () => {
        events.push('upload');
        afterUpload();
      },
    },
    '@/location/debugState': {
      reportLocationDebugEvent: (name, detail) => diagnostics.push({ name, detail }),
    },
  });
  await mod.runForegroundRuntimeWork({ reason: 'location_batch' });
  assert.deepEqual(events, ['DigitalBrainRuntimeWork', 'persist', 'upload']);
  const finished = diagnostics.find((event) => event.name === 'foreground_runtime_work_finished');
  assert.equal(finished.detail.payload.reason, 'location_batch');
  assert.equal(finished.detail.payload.deviceHealth.batteryPercent, 72);
  assert.ok(finished.detail.payload.durationMs >= 0);
  healthFails = true;
  locationEnabled = false;
  glassesEnabled = true;
  events.length = 0;
  await mod.runForegroundRuntimeWork();
  assert.deepEqual(events, ['persist'], 'Glasses alone must not turn location uploads back on');

  bluetoothGranted = true;
  transferFails = true;
  events.length = 0;
  await mod.runForegroundRuntimeWork();
  assert.deepEqual(
    events,
    ['persist', 'connect'],
    'Location storage failure must not block glasses recovery',
  );

  transferFails = false;
  locationEnabled = true;
  afterUpload = () => {
    glassesEnabled = false;
  };
  events.length = 0;
  await mod.runForegroundRuntimeWork();
  assert.deepEqual(
    events,
    ['persist', 'upload'],
    'Disconnect during an upload must not be undone by an old ownership snapshot',
  );

  locationEnabled = false;
  glassesEnabled = true;
  afterPermissionCheck = () => {
    glassesEnabled = false;
  };
  events.length = 0;
  await mod.runForegroundRuntimeWork();
  assert.deepEqual(events, ['persist'], 'Recheck ownership after asynchronous permission checks');
}
async function permissionRace() {
  const owners = [],
    events = [],
    definitions = new Map();
  let preference = true,
    grantPermission;
  let foreground = 'denied';
  const native = { setRuntimeLocationEnabled: async (enabled) => owners.push(enabled) };
  const location = load('location/backgroundLocation.ts', {
    '@react-native-async-storage/async-storage': {},
    'expo-location': {
      hasStartedLocationUpdatesAsync: async () => true,
      stopLocationUpdatesAsync: async () => events.push('stop-legacy-location'),
      hasStartedGeofencingAsync: async () => true,
      stopGeofencingAsync: async () => events.push('stop-legacy-geofence'),
      getForegroundPermissionsAsync: async () => ({ status: foreground }),
      requestForegroundPermissionsAsync: () =>
        new Promise((resolve) => {
          grantPermission = resolve;
        }),
      getBackgroundPermissionsAsync: async () => ({ status: 'granted' }),
    },
    'expo-task-manager': {
      isTaskDefined: () => false,
      defineTask: (name, task) => definitions.set(name, task),
    },
    'react-native': { Platform: { OS: 'android' } },
    '@/location/backgroundLocationQueue': {},
    '@/location/backgroundLocationDrainTask': {
      ensureBackgroundLocationDrainTaskRegistered: async () => events.push('register-drain'),
      unregisterBackgroundLocationDrainTask: async () => events.push('unregister-drain'),
    },
    '@/location/backgroundLocationTaskNames': {
      BACKGROUND_LOCATION_TASK: 'location',
      BACKGROUND_LOCATION_DRAIN_TASK: 'drain',
      BACKGROUND_LOCATION_GEOFENCE_TASK: 'geofence',
    },
    '@/location/debugState': debug,
    '@/api/client': { API_BASE_URL: 'https://api.example.com' },
    '@/location/runtimeState': runtime,
    '@/location/foregroundLocation': { hasSharedLocationRuntime: () => true },
    '@/modules/digital-brain-glasses-alerts/src': native,
    '@/location/trackingPreference': { isLocationTrackingEnabled: async () => preference },
  });
  const openingPermission = location.syncBackgroundLocationTracking(true);
  while (!grantPermission) await new Promise(setImmediate);
  await location.syncBackgroundLocationTracking(false);
  grantPermission({ status: 'granted' });
  await openingPermission;
  assert.deepEqual(
    owners,
    [false],
    'Late permission reply must not restart after sign-out/disable',
  );
  foreground = 'granted';
  await location.syncBackgroundLocationTracking(true);
  assert.equal(owners.at(-1), true);
  assert.ok(events.includes('stop-legacy-location') && events.includes('stop-legacy-geofence'));
  preference = false;
  await location.syncBackgroundLocationTracking(true);
  assert.equal(owners.at(-1), false, 'App resume honors independent location toggle');
  const before = owners.length;
  await definitions.get('location')({});
  await definitions.get('geofence')({});
  assert.equal(owners.length, before, 'Late legacy callbacks cannot resurrect Expo tracking');
}
async function debugExportBounds() {
  const reads = [],
    moves = [],
    writes = [];
  let size = 300 * 1024 * 1024;
  const tail =
    'partial line\n' +
    JSON.stringify({
      at: '2026-09-13T10:00:00Z',
      eventName: 'foreground_runtime_work_finished',
      payload: { durationMs: 10 },
    }) +
    '\n';
  const mod = load(
    'location/debugState.ts',
    {
      '@react-native-async-storage/async-storage': {
        getItem: async () => null,
        setItem: async () => {},
      },
      buffer: require('node:buffer'),
      'expo-file-system/legacy': {
        documentDirectory: 'file:///test/',
        EncodingType: { Base64: 'base64', UTF8: 'utf8' },
        getInfoAsync: async () => ({ exists: true, size }),
        readAsStringAsync: async (_uri, options) => {
          reads.push(options);
          return Buffer.from(tail).toString('base64');
        },
        makeDirectoryAsync: async () => {},
        deleteAsync: async () => {},
        moveAsync: async (options) => {
          moves.push(options);
          size = 0;
        },
        writeAsStringAsync: async (_uri, text) => {
          writes.push(text);
          size += Buffer.byteLength(text);
        },
      },
    },
    { console: { info() {}, warn() {} } },
  );
  const exported = await mod.readLocationDebugLogText({ backgroundOnly: true });
  assert.match(exported, /older file history was omitted/);
  assert.match(exported, /foreground_runtime_work_finished/);
  assert.equal(reads[0].encoding, 'base64');
  assert.equal(reads[0].length, 256 * 1024);
  assert.equal(reads[0].position, 300 * 1024 * 1024 - 256 * 1024);
  mod.reportLocationDebugEvent('background_sync_error', { payload: { huge: 'x'.repeat(100_000) } });
  await new Promise(setImmediate);
  assert.equal(moves.length, 1, 'Oversized legacy logs rotate without a full-file read');
  assert.ok(Buffer.byteLength(writes[0]) < 16 * 1024, 'Large diagnostics cannot defeat log limits');
  assert.equal(mod.getLocationDebugSnapshot().lastPayload.truncated, true);
  assert.equal(reads.length, 1, 'Log rotation must not read historical contents');
}

(async () => {
  await debugExportBounds();
  await handoff();
  await draining();
  await work();
  await permissionRace();
  console.log(
    'PASS background runtime: durable handoff/replay, serialized bounded upload, offline recovery, feature isolation',
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
