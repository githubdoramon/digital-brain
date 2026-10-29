/* global __dirname, setTimeout, clearTimeout, AbortController */
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
      process: { env: { EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID: 'fake-web-client-id' } },
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
const runtime = {
  getLocationRuntimeState: () => ({ appState: 'background', lastAppStateChangeAt: null }),
};
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
    queue.drainQueuedBackgroundLocations('manual'),
    queue.drainQueuedBackgroundLocations('background_task_worker'),
  ]);
  assert.equal(posts, 3, 'Concurrent worker triggers share one 45-second budget');
  assert.equal(JSON.parse(disk).length, 5, 'Budget exhaustion preserves remaining samples');
  fail = true;
  await queue.drainQueuedBackgroundLocations('manual');
  assert.equal(JSON.parse(disk).length, 5, 'Offline upload preserves queue');
  fail = false;
  await queue.drainQueuedBackgroundLocations('manual');
  assert.equal(JSON.parse(disk).length, 2, 'Later runtime opportunity resumes delivery');
}
async function permissionRace() {
  const owners = [],
    events = [],
    definitions = new Map();
  let preference = true,
    grantPermission;
  let foreground = 'denied';
  const native = {
    setRuntimeLocationEnabled: async (enabled) => owners.push(enabled),
    configureRuntimeLocationUploader: async () => events.push('configure-native-uploader'),
  };
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
    '@/location/backgroundLocationQueue': {
      drainQueuedBackgroundLocations: async () => events.push('legacy-queue-drain'),
    },
    '@/location/backgroundLocationDrainTask': {
      ensureBackgroundLocationDrainTaskRegistered: async () => events.push('register-drain'),
      unregisterBackgroundLocationDrainTask: async () => events.push('unregister-drain'),
    },
    '@/location/backgroundLocationTaskNames': {
      BACKGROUND_LOCATION_TASK: 'location',
      BACKGROUND_LOCATION_DRAIN_TASK: 'drain',
      BACKGROUND_LOCATION_GEOFENCE_TASK: 'geofence',
    },
    '@/api/client': { API_BASE_URL: 'https://api.example.com' },
    '@/location/runtimeState': runtime,
    '@/location/foregroundLocation': { hasSharedLocationRuntime: () => true },
    '@/modules/digital-brain-runtime/src': native,
    '@/location/trackingPreference': { isLocationTrackingEnabled: async () => preference },
    process: { env: { EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID: 'fake-web-client-id' } },
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
async function retiredDebugCleanup() {
  const removed = [];
  const mod = load('debug/retiredDebugArtifacts.ts', {
    '@react-native-async-storage/async-storage': {
      removeItem: async (key) => removed.push(key),
    },
    'expo-file-system/legacy': {
      documentDirectory: 'file:///test/',
      deleteAsync: async (uri, options) => {
        assert.equal(options.idempotent, true);
        removed.push(uri);
      },
    },
  });
  await mod.clearRetiredDebugArtifacts();
  assert.deepEqual(
    removed.sort(),
    [
      'digitalbrain.locationDebugSnapshot',
      'file:///test/digital-brain-location-debug-log.jsonl',
      'file:///test/digital-brain-location-debug-log.jsonl.previous',
      'file:///test/event-photo-debug-log.txt',
      'file:///test/voice-debug/',
      'file:///test/whisper/',
    ].sort(),
    'Upgrade deletes only retired private debug artifacts',
  );
}

(async () => {
  await retiredDebugCleanup();
  await draining();
  await permissionRace();
  console.log(
    'PASS background runtime: serialized bounded legacy queue upload, offline recovery, permission race',
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
