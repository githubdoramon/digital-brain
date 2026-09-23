/* Real coordinator/index/screen with delayed native boundaries: lifecycle regressions. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(setImmediate);
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function load(file, mocks) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
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
      Error,
      setInterval,
      clearInterval,
      require(name) {
        if (name in mocks) return mocks[name];
        throw new Error(`Unexpected import ${name}`);
      },
    },
    { filename: file },
  );
  return module.exports;
}
function fixture(initial = []) {
  let disk = JSON.stringify(initial),
    active = null,
    finished,
    playbackFinished;
  const calls = { starts: 0, stops: 0, stats: 0, resume: 0, playbackStops: 0, pause: [] };
  const controls = {};
  const storage = {
    getItem: async () => {
      if (controls.read) await controls.read.promise;
      return disk;
    },
    setItem: async (_key, value) => {
      if (controls.write) await controls.write.promise;
      disk = value;
    },
  };
  const library = load('mentraCapture/recordingLibrary.ts', {
    '@react-native-async-storage/async-storage': storage,
  });
  const recordingFs = {
    getInfoAsync: async () => {
      calls.stats++;
      if (controls.stat) return controls.stat.promise;
      return { exists: true, size: 1000 };
    },
    deleteAsync: async () => {},
    StorageAccessFramework: {
      createFileAsync: async () =>
        controls.file ? controls.file.promise : `content://test/recording-${calls.starts + 1}`,
    },
  };
  const sdk = {
    ensureMentraConnection: async () => true,
    getMentraConnectionStatus: async () => ({ connected: true, deviceModel: 'Mentra Live' }),
    getGlassesM4aRecordingStatus: async () =>
      controls.status
        ? controls.status.promise
        : active
          ? { recording: true, ...active }
          : { recording: false },
    recoverGlassesM4aRecording: async () => ({ recovered: false }),
    startGlassesM4aRecording: async (uri) => {
      calls.starts++;
      if (controls.nativeStart) await controls.nativeStart.promise;
      active = { outputUri: uri, startedAt: Date.now() };
      return active;
    },
    stopGlassesM4aRecording: async () => {
      calls.stops++;
      if (controls.nativeStop) await controls.nativeStop.promise;
      const result = { ...active, completed: true, durationMs: 1000, reason: 'user_stopped' };
      active = null;
      return result;
    },
    setMentraMicState: async (enabled) => {
      if (!enabled && controls.micOff) await controls.micOff.promise;
    },
    subscribeGlassesM4aRecordingFinished: (fn) => {
      finished = fn;
    },
    subscribeGlassesM4aPlaybackFinished: (fn) => {
      playbackFinished = fn;
    },
    playGlassesM4aRecording: async () => ({}),
    stopGlassesM4aPlayback: async () => {
      calls.playbackStops++;
    },
  };
  const coordinator = load('mentraCapture/recordings.ts', {
    'expo-file-system/legacy': recordingFs,
    'react-native': { Platform: { OS: 'android' } },
    '@/modules/digital-brain-storage/src': {
      renameDocument: async (uri) => ({ uri: `${uri}-renamed` }),
    },
    '@/storage/digitalBrainStorage': {
      DigitalBrainStorageFolder: { Recordings: 'Recordings' },
      getDigitalBrainStorageFolder: async () => 'content://test/folder',
      safeStorageFileName: (name) => name.replace(/[/]/g, '_'),
    },
    './recordingLibrary': library,
    './debug': { appendMentraDebugLog: async () => {} },
    './sdk': sdk,
    './wakeWord': {
      pauseWakeWordListening: async (...args) => {
        calls.pause.push(args);
        if (controls.pause) await controls.pause.promise;
      },
      resumeWakeWordListening: async () => {
        calls.resume++;
      },
    },
  });
  return {
    coordinator,
    controls,
    calls,
    library,
    disk: () => JSON.parse(disk),
    finished: (result) => finished(result),
    playbackFinished: (uri) => playbackFinished(uri),
  };
}
const example = {
  id: 'test-audio',
  uri: 'content://test/saved',
  name: 'Test recording.m4a',
  startedAt: '2026-01-01T12:00:00.000Z',
  durationMs: 1000,
  sizeBytes: 1000,
};
async function contracts() {
  const f = fixture(),
    c = f.coordinator;
  f.controls.nativeStart = deferred();
  const start = c.startGlassesAudioRecording();
  assert.equal(start, c.startGlassesAudioRecording());
  await tick();
  assert.equal(f.calls.starts, 1);
  assert.equal(c.getGlassesAudioRecordingState().phase, 'starting');
  assert.equal(f.calls.pause[0][1].keepMicrophoneEnabled, true);
  f.controls.nativeStart.resolve();
  await start;
  const firstUri = c.getGlassesAudioRecordingState().outputUri;
  f.controls.nativeStop = deferred();
  f.controls.stat = deferred();
  const stopping = c.stopGlassesAudioRecording();
  assert.equal(stopping, c.stopGlassesAudioRecording());
  assert.equal(c.getGlassesAudioRecordingState().phase, 'stopping');
  f.controls.nativeStop.resolve();
  const { saved } = await stopping;
  assert.equal(c.getGlassesAudioRecordingState().phase, 'idle');
  assert.equal(c.getGlassesAudioRecordingState().savingCount, 1);
  await c.startGlassesAudioRecording();
  const nextUri = c.getGlassesAudioRecordingState().outputUri;
  assert.notEqual(nextUri, firstUri);
  f.controls.stat.resolve({ exists: true, size: 1000 });
  await saved;
  assert.equal(
    c.getGlassesAudioRecordingState().outputUri,
    nextUri,
    'old save cannot clear new capture',
  );
  const resumes = f.calls.resume;
  f.finished({ completed: true, outputUri: firstUri, startedAt: Date.now() });
  await tick();
  assert.equal(c.getGlassesAudioRecordingState().outputUri, nextUri);
  assert.equal(f.calls.resume, resumes, 'old completion cannot resume wake listening');
  assert.equal((await c.listGlassesAudioRecordings()).length, 1);
  await c.stopGlassesAudioRecording();
  console.log(
    'PASS rapid taps, phases, mic handoff, slow save and late duplicate during new capture',
  );

  const h = fixture();
  h.controls.status = deferred();
  const hydration = h.coordinator.hydrateGlassesAudioRecording();
  await h.coordinator.startGlassesAudioRecording();
  h.controls.status.resolve({ recording: false });
  await hydration;
  assert.equal(h.coordinator.getGlassesAudioRecordingState().recording, true);
  await h.coordinator.stopGlassesAudioRecording();
  const e = fixture();
  await e.coordinator.startGlassesAudioRecording();
  e.controls.nativeStop = deferred();
  const failure = e.coordinator.stopGlassesAudioRecording();
  e.controls.nativeStop.reject(new Error('native stop failed'));
  await assert.rejects(failure, /native stop failed/);
  assert.equal(e.coordinator.getGlassesAudioRecordingState().recording, true);
  assert.equal(e.coordinator.getGlassesAudioRecordingState().phase, 'recording');
  e.controls.nativeStop = null;
  e.controls.stat = deferred();
  const failedSave = await e.coordinator.stopGlassesAudioRecording();
  e.controls.stat.reject(new Error('provider unavailable'));
  assert.equal(await failedSave.saved, null);
  assert.match(e.coordinator.getGlassesAudioRecordingState().lastError, /provider unavailable/);
  assert.equal(e.coordinator.getGlassesAudioRecordingState().savingCount, 0);
  console.log(
    'PASS stale hydration, failed native stop preserves Stop and save errors are visible',
  );

  const l = fixture([example]);
  await l.coordinator.listGlassesAudioRecordings();
  assert.equal(l.calls.stats, 0, 'listing never scans every provider file');
  l.controls.write = deferred();
  const rename = l.coordinator.renameGlassesAudioRecording(example, 'Renamed test');
  await tick();
  const saving = l.library.updateRecordingLibrary((current) => [
    ...current,
    { ...example, id: 'second', uri: 'content://test/second' },
  ]);
  l.controls.write.resolve();
  await Promise.all([rename, saving]);
  assert.equal(l.disk().length, 2);
  assert.equal(l.disk().find((item) => item.id === example.id).name, 'Renamed test.m4a');
  await Promise.all([
    l.coordinator.deleteGlassesAudioRecording(example),
    l.library.updateRecordingLibrary((current) => [
      ...current,
      { ...example, id: 'third', uri: 'content://test/third' },
    ]),
  ]);
  assert.equal(l.disk().length, 2);
  assert.equal(
    l.disk().some((item) => item.id === example.id),
    false,
  );
  console.log('PASS index-only listing and overlapping rename/delete/save mutations');

  const p = fixture([example]);
  p.coordinator.subscribeGlassesAudioRecording(() => {});
  await p.coordinator.playOrStopGlassesAudioRecording(example);
  assert.equal(p.coordinator.getGlassesAudioRecordingState().isPlayingUri, example.uri);
  await p.coordinator.deleteGlassesAudioRecording({
    ...example,
    id: 'unrelated',
    uri: 'content://test/unrelated',
  });
  assert.equal(p.calls.playbackStops, 0);
  p.playbackFinished(example.uri);
  assert.equal(p.coordinator.getGlassesAudioRecordingState().isPlayingUri, null);
  await p.coordinator.startGlassesAudioRecording();
  await assert.rejects(p.coordinator.playOrStopGlassesAudioRecording(example), /Stop recording/);
  await p.coordinator.stopGlassesAudioRecording();
  console.log('PASS playback completion, unrelated delete and capture/playback exclusion');
}
async function wakeHandoffContract() {
  const mic = [];
  let connection;
  const wake = load('mentraCapture/wakeWord.ts', {
    'expo-asset': {
      Asset: {
        fromModule: () => ({ downloadAsync: async () => {}, localUri: 'file://test/model' }),
      },
    },
    'react-native': { Platform: { OS: 'android' } },
    '@/modules/digital-brain-glasses-alerts/src': {
      startGlassesWakeRuntime: async () => {},
      stopGlassesWakeRuntime: async () => {},
      initializeV8WakeSpotter: async () => {},
      releaseV8WakeSpotter: async () => {},
    },
    '@/mentraCapture/sdk': {
      getMentraConnectionStatus: async () => ({ connected: true }),
      setMentraMicState: async (enabled) => {
        mic.push(enabled);
      },
      subscribeMentraConnectionState: (listener) => {
        connection = listener;
        return () => {};
      },
      subscribeMentraMicPcm: () => () => {},
      subscribeMentraVideoRecordingStatus: () => () => {},
    },
    '@/mentraCapture/debug': {
      appendMentraDebugLog: async () => {},
      appendWakeCommandDebugLog: async () => {},
    },
    '@/mentraCapture/commandTranscription': {
      cancelGlassesCommandTranscription() {},
      warmGlassesCommandTranscription: async () => {},
    },
    '@/mentraCapture/glassesCommandAgent': {},
    '@/wakeWord': {
      V8TwoStageWakeWordDetector: class {
        reset() {}
      },
      OpenWakeWordOnnxBackend: { create: async () => ({}) },
    },
    '@/assets/wake-word/hey-brain-v8.json': { audioConfig: { streamHopSamples: 1280 } },
    '@/assets/wake-word/melspectrogram.onnx': 1,
    '@/assets/wake-word/embedding_model.onnx': 2,
    'onnxruntime-react-native': {},
  });
  await wake.initializeWakeWordRuntime();
  assert.deepEqual(mic, [true]);
  await wake.pauseWakeWordListening('audio_recording', { keepMicrophoneEnabled: true });
  connection({ connected: true });
  await tick();
  assert.deepEqual(
    mic,
    [true],
    'audio handoff/reconcile never cycles the mic or restarts detection',
  );
  await wake.resumeWakeWordListening('audio_recording', 'test_finished');
  assert.deepEqual(mic, [true, true]);
  await wake.pauseWakeWordListening('video_recording');
  connection({ connected: true });
  await tick();
  assert.deepEqual(mic, [true, true], 'video also retains mic ownership across reconcile');
  await wake.resumeWakeWordListening('video_recording', 'test_finished');
  await wake.pauseWakeWordListening('firmware_update');
  assert.equal(mic.at(-1), false, 'firmware pause still turns mic off');
  await wake.disposeWakeWordRuntime();
  console.log('PASS real wake coordinator: audio/video handoff and firmware mic shutdown');
}

async function screenContract() {
  const React = require('react');
  const { create, act } = require('react-test-renderer');
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const f = fixture();
  f.controls.read = deferred();
  const storage = deferred(),
    router = { back() {}, push() {} },
    notices = { showError() {}, showSuccess() {} };
  const Screen = load('app/settings/glasses-recordings/index.tsx', {
    react: React,
    'react/jsx-runtime': require('react/jsx-runtime'),
    '@expo/vector-icons/Ionicons': 'Icon',
    '@react-navigation/native': {
      useFocusEffect: (effect) => React.useEffect(effect, [effect]),
      useIsFocused: () => true,
    },
    'expo-router': { useRouter: () => router },
    'react-native': {
      AppState: { addEventListener: () => ({ remove: () => undefined }) },
      Alert: {},
      KeyboardAvoidingView: 'KeyboardAvoidingView',
      Platform: { OS: 'android' },
      ScrollView: 'ScrollView',
      StyleSheet: { create: (s) => s },
      Text: 'Text',
      TextInput: 'TextInput',
      View: 'View',
    },
    'react-native-safe-area-context': { useSafeAreaInsets: () => ({ top: 0, bottom: 0 }) },
    '@/components/AppPressable': { AppPressable: 'Pressable' },
    '@/components/Button': { Button: 'Button' },
    '@/components/Card': { Card: 'Card' },
    '@/hooks/useAppNotice': { useAppNotice: () => notices },
    '@/mentraCapture': f.coordinator,
    '@/storage/digitalBrainStorage': { getDigitalBrainStorageBaseUri: () => storage.promise },
    '@/theme': { theme: { colors: {}, radius: {} } },
  }).default;
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(Screen));
  });
  assert.ok(!JSON.stringify(renderer.toJSON()).includes('Choose storage first'));
  await act(async () => {
    storage.resolve('content://test/base');
    await tick();
  });
  assert.ok(!JSON.stringify(renderer.toJSON()).includes('Choose storage first'));
  const button = () =>
    renderer.root.findAllByType('Button').find((node) => /recording/.test(node.props.label));
  assert.equal(button().props.disabled, false, 'slow library cannot block record');
  f.controls.nativeStart = deferred();
  await act(async () => {
    button().props.onPress();
    await tick();
  });
  assert.equal(button().props.label, 'Starting recording…');
  assert.equal(button().props.disabled, true);
  assert.notEqual(button().props.loading, true);
  await act(async () => {
    f.controls.nativeStart.resolve();
    await tick();
  });
  assert.equal(button().props.label, 'Stop recording');
  f.controls.stat = deferred();
  await act(async () => {
    button().props.onPress();
    await tick();
  });
  assert.equal(button().props.label, 'Start recording');
  assert.equal(button().props.disabled, false);
  assert.ok(JSON.stringify(renderer.toJSON()).includes('You can start another recording'));
  await act(async () => {
    f.controls.stat.resolve({ exists: true, size: 1000 });
    f.controls.read.resolve();
    await tick();
    renderer.unmount();
  });
  console.log('PASS screen: folder loading, slow library/save and visible control labels');
}
(async () => {
  await contracts();
  await wakeHandoffContract();
  await screenContract();
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
