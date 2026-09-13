/* Host-side behavior tests: real TypeScript modules, mocked native/OS boundaries. */
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
      setInterval: () => 1,
      require(name) {
        if (name in mocks) return mocks[name];
        throw new Error(`Unexpected import ${name}`);
      },
      ...globals,
    },
    { filename: file },
  );
  return module.exports;
}

const debug = { appendMentraDebugLog: async () => {}, appendWakeCommandDebugLog: async () => {} };
const tick = () => new Promise(setImmediate);

function fixture({ persisted = null, startError = false, writeError = false } = {}) {
  const disk = { value: persisted };
  const maintenance = load('mentraCapture/maintenance.ts', {
    '@react-native-async-storage/async-storage': {
      getItem: async () => disk.value,
      setItem: async (_key, value) => {
        if (writeError) throw new Error('storage failure');
        disk.value = value;
      },
      removeItem: async () => {
        disk.value = null;
      },
    },
  });
  let callback;
  const calls = { start: 0, pause: 0, resume: 0, query: 0, reconnect: 0 };
  const controls = {
    cameraBusy: false,
    connectionBusy: false,
    recording: false,
    capturing: false,
    syncing: false,
    queryStatus: 'idle',
    available: true,
  };
  const device = {
    connected: true,
    wifiConnected: true,
    batteryLevel: 80,
    appVersion: '1',
    besVersion: '2',
    mtkVersion: '3',
  };
  const transport = {
    getDevice: async () => device,
    reconnect: async () => {
      calls.reconnect++;
      device.connected = true;
    },
    check: async () => controls.available,
    start: async () => {
      calls.start++;
      assert.equal(
        maintenance.isGlassesMaintenanceActive(),
        true,
        'maintenance precedes native dispatch',
      );
      assert.ok(disk.value, 'maintenance is persisted before dispatch');
      if (startError) throw new Error('start ACK timeout');
    },
    query: async () => {
      calls.query++;
      return { status: controls.queryStatus, overall_percent: 0 };
    },
    refreshVersions: async () => {},
    cameraBusy: async () => controls.cameraBusy,
    connectionBusy: () => controls.connectionBusy,
    subscribe: (listener) => {
      callback = listener;
      return () => {};
    },
  };
  const firmware = load('mentraCapture/firmware.ts', {
    './debug': debug,
    './maintenance': maintenance,
    './sdk': { getGlassesFirmwareTransport: () => transport },
    './wakeWord': {
      pauseWakeWordListening: async () => {
        calls.pause++;
      },
      resumeWakeWordListening: async () => {
        calls.resume++;
      },
    },
    './recordings': { getGlassesAudioRecordingState: () => ({ recording: controls.recording }) },
    './imageEnhancement': { getImageEnhancementStatus: () => ({ running: controls.capturing }) },
    './sync': { getCaptureSyncStatus: () => ({ running: controls.syncing }) },
  });
  return {
    firmware,
    maintenance,
    controls,
    calls,
    device,
    disk,
    transport,
    emit: async (status) => {
      callback(status);
      await tick();
    },
  };
}

async function firmwareContracts() {
  const f = fixture();
  await f.firmware.refreshGlassesFirmware();
  assert.equal(f.firmware.getGlassesFirmwareState().phase, 'available');
  for (const property of ['cameraBusy', 'connectionBusy', 'recording', 'capturing', 'syncing']) {
    f.controls[property] = true;
    await assert.rejects(f.firmware.installGlassesFirmware());
    assert.equal(f.calls.start, 0);
    assert.equal(f.maintenance.isGlassesMaintenanceActive(), false);
    f.controls[property] = false;
    await f.firmware.refreshGlassesFirmware();
  }
  f.device.batteryLevel = 49;
  await assert.rejects(f.firmware.installGlassesFirmware(), /50%/);
  f.device.batteryLevel = 80;
  await f.firmware.refreshGlassesFirmware();
  f.device.wifiConnected = false;
  await assert.rejects(f.firmware.installGlassesFirmware(), /Wi-Fi/);
  f.device.wifiConnected = true;
  await f.firmware.refreshGlassesFirmware();
  await f.emit({ status: 'complete', session_id: 'previous-install', overall_percent: 100 });
  await f.firmware.installGlassesFirmware();
  await f.emit({ status: 'complete', session_id: 'previous-install', overall_percent: 100 });
  assert.equal(
    f.maintenance.isGlassesMaintenanceActive(),
    true,
    'old session completion cannot unlock the current install',
  );
  assert.equal(f.calls.start, 1);
  assert.equal(f.firmware.getGlassesFirmwareState().phase, 'updating', 'ack is not completion');
  await assert.rejects(f.firmware.installGlassesFirmware());
  await f.emit({ status: 'step_complete', overall_percent: 50 });
  assert.equal(
    f.maintenance.isGlassesMaintenanceActive(),
    true,
    'intermediate step keeps ownership',
  );
  await f.emit({ status: 'complete', overall_percent: 100 });
  assert.equal(f.maintenance.isGlassesMaintenanceActive(), false);
  assert.equal(f.disk.value, null);
  assert.equal(f.calls.resume, 1);

  const uncertain = fixture({ startError: true });
  await uncertain.firmware.refreshGlassesFirmware();
  await assert.rejects(uncertain.firmware.installGlassesFirmware(), /timeout/);
  assert.equal(uncertain.maintenance.isGlassesMaintenanceActive(), true);
  assert.equal(uncertain.firmware.getGlassesFirmwareState().phase, 'awaiting_status');
  await uncertain.firmware.refreshGlassesFirmware();
  assert.equal(uncertain.calls.start, 1, 'refresh never replays an uncertain install');
  assert.equal(
    uncertain.maintenance.isGlassesMaintenanceActive(),
    true,
    'early idle cannot release an in-flight start',
  );
  const resumed = fixture({ persisted: uncertain.disk.value });
  await resumed.firmware.initializeGlassesFirmware();
  await assert.rejects(resumed.maintenance.assertGlassesNotUpdating());
  resumed.device.connected = false;
  resumed.controls.queryStatus = 'in_progress';
  await resumed.firmware.refreshGlassesFirmware();
  assert.equal(resumed.calls.start, 0, 'app restart reattaches instead of restarting installation');
  assert.equal(
    resumed.calls.reconnect,
    1,
    'status-only reconnect restores a cold native controller',
  );
  await resumed.emit({ status: 'failed', error_message: 'Test update rejected' });
  assert.equal(resumed.maintenance.isGlassesMaintenanceActive(), false);

  const diskFailure = fixture({ writeError: true });
  await diskFailure.firmware.refreshGlassesFirmware();
  await assert.rejects(diskFailure.firmware.installGlassesFirmware(), /storage/);
  assert.equal(diskFailure.calls.start, 0);
  assert.equal(diskFailure.maintenance.isGlassesMaintenanceActive(), false);

  const fastCompletion = fixture();
  await fastCompletion.firmware.refreshGlassesFirmware();
  fastCompletion.transport.start = async () => {
    await fastCompletion.emit({ status: 'complete', overall_percent: 100 });
  };
  await fastCompletion.firmware.installGlassesFirmware();
  assert.equal(
    fastCompletion.firmware.getGlassesFirmwareState().phase,
    'complete',
    'late start ack cannot overwrite terminal progress',
  );
  const rebootAfterCompletion = fixture();
  await rebootAfterCompletion.firmware.refreshGlassesFirmware();
  await rebootAfterCompletion.firmware.installGlassesFirmware();
  rebootAfterCompletion.controls.queryStatus = 'complete';
  rebootAfterCompletion.transport.refreshVersions = async () => {
    throw new Error('Glasses are restarting');
  };
  await rebootAfterCompletion.firmware.refreshGlassesFirmware();
  assert.equal(
    rebootAfterCompletion.firmware.getGlassesFirmwareState().phase,
    'complete',
    'a completion query must not immediately request versions during the final reboot',
  );
  console.log(
    'PASS firmware: preconditions, barriers, progress, failure, ACK race, persistence and no replay',
  );
}

async function connectionContracts() {
  const events = new Map();
  const device = { id: 'test-device', name: 'Mentra Live Test', model: 'Mentra Live' };
  const calls = [];
  let releaseDisconnect;
  const native = {
    addListener: (name, listener) => {
      events.set(name, listener);
      return { remove() {} };
    },
    getDefaultDevice: async () => device,
    setDefaultDevice: async () => {},
    getGlassesStatus: async () => ({
      connection: { state: 'connected', fullyBooted: true },
      deviceModel: 'Mentra Live',
    }),
    connect: async () => {
      calls.push('connect');
    },
    connectDefault: async () => {
      calls.push('connectDefault');
    },
    disconnect: async () => {
      calls.push('disconnect');
      if (releaseDisconnect) await releaseDisconnect.promise;
    },
    setGalleryModeEnabled: async () => {
      calls.push('gallery');
    },
    setPhotoCaptureDefaults: async () => {},
    setVideoRecordingDefaults: async () => {},
    setMaxVideoRecordingDuration: async () => {},
  };
  const sdk = load(
    'mentraCapture/sdk.ts',
    {
      './debug': debug,
      './maintenance': {
        assertGlassesNotUpdating: async () => {},
        isGlassesMaintenanceActive: () => false,
      },
      '@react-native-async-storage/async-storage': {
        getItem: async () => null,
        setItem: async () => {},
      },
      buffer: require('node:buffer'),
      'expo-file-system/legacy': {},
      'expo-modules-core': {},
      'react-native': { Platform: { OS: 'web' }, PermissionsAndroid: {} },
      '@/glassesAlerts/runtime': { setExpectedGlassesAlertAudioDevice: async () => {} },
      '@mentra/bluetooth-sdk': { BluetoothSdk: native },
      '@mentra/bluetooth-sdk/internal': { default: native },
    },
    {
      setTimeout: (callback) => {
        queueMicrotask(callback);
        return 1;
      },
    },
  );
  let resolve;
  releaseDisconnect = {
    promise: new Promise((r) => {
      resolve = r;
    }),
  };
  const pairing = sdk.pairGlasses(device);
  await tick();
  const foreground = sdk.ensureMentraConnection();
  const capture = sdk.ensureMentraConnection({ applyCaptureDefaults: false });
  await tick();
  assert.deepEqual(
    calls,
    ['disconnect'],
    'pairing owns the controller while Android releases GATT',
  );
  resolve();
  await Promise.all([pairing, foreground, capture]);
  assert.equal(calls.filter((value) => value === 'connect').length, 1);
  assert.equal(calls.filter((value) => value === 'gallery').length, 1);
  releaseDisconnect = null;
  calls.length = 0;
  await sdk.recoverMentraConnection();
  assert.deepEqual(
    calls,
    ['disconnect', 'connect', 'gallery'],
    'explicit repair reconnects even when stale status says ready',
  );
  calls.length = 0;
  for (let i = 0; i < 3; i++) {
    events.get('glasses_link_unhealthy')({ reason: 'heartbeat_timeout' });
    await tick();
  }
  assert.equal(
    calls.filter((value) => value === 'connect').length,
    2,
    'automatic recovery is bounded',
  );
  console.log(
    'PASS connection: pair/foreground/capture serialization, explicit repair and bounded automatic recovery',
  );
}

function playbackSubscriptionContracts() {
  const events = new Map();
  let removals = 0;
  const native = {
    addListener: (name, listener) => {
      events.set(name, listener);
      return {
        remove: () => {
          events.delete(name);
          removals++;
        },
      };
    },
  };
  // Use the installed SDK's real public facade: permissive mocks missed this startup crash.
  const publicSdk = load('node_modules/@mentra/bluetooth-sdk/src/index.ts', {
    './_private/BluetoothSdkModule': { default: native, __esModule: true },
    './BluetoothSdk.types': {},
  });
  assert.throws(
    () => publicSdk.BluetoothSdk.addListener('glasses_audio_playback_finished', () => {}),
    /Unsupported BluetoothSdk event/,
  );
  const mocks = {
    './debug': debug,
    './maintenance': {
      assertGlassesNotUpdating: async () => {},
      isGlassesMaintenanceActive: () => false,
    },
    '@react-native-async-storage/async-storage': {
      default: { getItem: async () => null },
      __esModule: true,
    },
    buffer: require('node:buffer'),
    'expo-file-system/legacy': {},
    'expo-modules-core': {},
    'react-native': { Platform: { OS: 'web' }, PermissionsAndroid: {} },
    '@/glassesAlerts/runtime': { setExpectedGlassesAlertAudioDevice: async () => {} },
    '@mentra/bluetooth-sdk': publicSdk,
    '@mentra/bluetooth-sdk/internal': { default: native, __esModule: true },
  };
  const sdk = load('mentraCapture/sdk.ts', mocks);
  const finished = [];
  const unsubscribe = sdk.subscribeGlassesM4aPlaybackFinished((uri) => finished.push(uri));
  events.get('glasses_audio_playback_finished')({ outputUri: 'file:///test-recording.m4a' });
  events.get('glasses_audio_playback_finished')({ outputUri: null });
  assert.deepEqual(finished, ['file:///test-recording.m4a']);
  unsubscribe();
  assert.equal(removals, 1);
  assert.equal(events.size, 0);
  const unavailable = load('mentraCapture/sdk.ts', {
    ...mocks,
    '@mentra/bluetooth-sdk/internal': null,
  });
  assert.doesNotThrow(() => unavailable.subscribeGlassesM4aPlaybackFinished(() => {})());
  console.log(
    'PASS playback: startup with real public event restrictions, native completion delivery and cleanup',
  );
}

(async () => {
  playbackSubscriptionContracts();
  await firmwareContracts();
  await connectionContracts();
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
