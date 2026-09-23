import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import DigitalBrainStorageNative from '@/modules/digital-brain-storage/src';
import {
  DigitalBrainStorageFolder,
  getDigitalBrainStorageBaseUri,
  getDigitalBrainStorageFolder,
  safeStorageFileName,
} from '@/storage/digitalBrainStorage';

import { appendMentraDebugLog } from './debug';
import {
  readRecordingLibrary,
  updateRecordingLibrary,
  type GlassesAudioRecording,
} from './recordingLibrary';
import {
  ensureMentraConnection,
  getMentraConnectionStatus,
  getGlassesM4aRecordingStatus,
  playGlassesM4aRecording,
  recoverGlassesM4aRecording,
  setMentraMicState,
  startGlassesM4aRecording,
  stopGlassesM4aPlayback,
  stopGlassesM4aRecording,
  subscribeGlassesM4aRecordingFinished,
  subscribeGlassesM4aPlaybackFinished,
  type GlassesM4aRecordingResult,
} from './sdk';
import { pauseWakeWordListening, resumeWakeWordListening } from './wakeWord';

export type { GlassesAudioRecording } from './recordingLibrary';

export enum GlassesAudioRecordingPhase {
  Idle = 'idle',
  Starting = 'starting',
  Recording = 'recording',
  Stopping = 'stopping',
}

export type GlassesAudioRecordingState = {
  phase: GlassesAudioRecordingPhase;
  recording: boolean;
  startedAt: number | null;
  outputUri: string | null;
  isPlayingUri: string | null;
  savingCount: number;
  libraryVersion: number;
  lastError: string | null;
};

type RecordingListener = (state: GlassesAudioRecordingState) => void;
export type GlassesAudioRecordingStopResult = { saved: Promise<GlassesAudioRecording | null> };

let state: GlassesAudioRecordingState = {
  phase: GlassesAudioRecordingPhase.Idle,
  recording: false,
  startedAt: null,
  outputUri: null,
  isPlayingUri: null,
  savingCount: 0,
  libraryVersion: 0,
  lastError: null,
};
const listeners = new Set<RecordingListener>();
let nativeCompletionSubscribed = false;
let nativeCompletionVersion = 0;
const nativeFinalizations = new Map<string, Promise<GlassesAudioRecording | null>>();
const completedFinalizations: string[] = [];
let captureGeneration = 0;
let startOperation: Promise<void> | null = null;
let stopOperation: Promise<GlassesAudioRecordingStopResult> | null = null;
let hydration: Promise<void> | null = null;
let recovery: Promise<void> | null = null;
let recovered = false;
let micDisableInFlight: Promise<void> = Promise.resolve();
let playbackOperations: Promise<void> = Promise.resolve();

function publish(next: Partial<GlassesAudioRecordingState>): void {
  state = { ...state, ...next };
  listeners.forEach((listener) => listener(state));
}

function debug(phase: string, startedAt: number): void {
  void appendMentraDebugLog('audio_recording_latency', {
    phase,
    elapsed_ms: Date.now() - startedAt,
  }).catch(() => undefined);
}

function recordingFileName(startedAt: Date): string {
  return `Mentra recording ${startedAt.toISOString().replace(/[:.]/g, '-')}.m4a`;
}

function clearCapture(): void {
  publish({
    phase: GlassesAudioRecordingPhase.Idle,
    recording: false,
    startedAt: null,
    outputUri: null,
  });
}

function resumeWakeWordAfterRecording(generation: number, reason: string): void {
  void micDisableInFlight
    .then(async () => {
      // A delayed save/mic-off from the previous session must never remove the
      // new recording's wake-listener pause or re-enable its microphone.
      if (generation !== captureGeneration || state.phase !== GlassesAudioRecordingPhase.Idle)
        return;
      await resumeWakeWordListening('audio_recording', reason);
    })
    .catch(() => undefined);
}

function finalizeNativeRecording(
  result: GlassesM4aRecordingResult,
): Promise<GlassesAudioRecording | null> {
  const existing = nativeFinalizations.get(result.outputUri);
  if (existing) return existing;
  const startedAt = Date.now();
  publish({ savingCount: state.savingCount + 1 });
  const completion = (async () => {
    if (!result.completed)
      throw new Error('The recording stopped before any audio could be saved.');
    const info = await FileSystem.getInfoAsync(result.outputUri);
    if (!info.exists || !('size' in info) || !info.size)
      throw new Error('The recording file could not be verified.');
    const date = new Date(result.startedAt ?? Date.now());
    const next: GlassesAudioRecording = {
      id: `mentra-audio:${result.outputUri}`,
      uri: result.outputUri,
      name: recordingFileName(date),
      startedAt: date.toISOString(),
      durationMs: result.durationMs ?? 0,
      sizeBytes: info.size,
    };
    await updateRecordingLibrary((current) => {
      // A late duplicate event must not undo a user rename.
      if (current.some((item) => item.id === next.id || item.uri === next.uri)) return current;
      return [next, ...current];
    });
    publish({ libraryVersion: state.libraryVersion + 1 });
    return next;
  })()
    .catch((error) => {
      publish({ lastError: error instanceof Error ? error.message : 'Could not save recording.' });
      return null;
    })
    .finally(() => {
      publish({ savingCount: state.savingCount - 1 });
      debug('index_saved', startedAt);
    });
  nativeFinalizations.set(result.outputUri, completion);
  // Keep a small completed-result cache too: native completion can arrive
  // after the explicit stop promise has already finished indexing.
  void completion.then(() => {
    completedFinalizations.push(result.outputUri);
    if (completedFinalizations.length > 32) {
      nativeFinalizations.delete(completedFinalizations.shift()!);
    }
  });
  return completion;
}

function subscribeNativeCompletionOnce(): void {
  if (nativeCompletionSubscribed) return;
  nativeCompletionSubscribed = true;
  subscribeGlassesM4aRecordingFinished((result) => {
    nativeCompletionVersion += 1;
    if (state.outputUri === result.outputUri) {
      const generation = captureGeneration;
      clearCapture();
      resumeWakeWordAfterRecording(generation, 'audio_recording_finished');
    }
    void finalizeNativeRecording(result);
  });
  subscribeGlassesM4aPlaybackFinished((uri) => {
    if (state.isPlayingUri === uri) publish({ isPlayingUri: null });
  });
}

export function getGlassesAudioRecordingState(): GlassesAudioRecordingState {
  return state;
}
export function subscribeGlassesAudioRecording(listener: RecordingListener): () => void {
  subscribeNativeCompletionOnce();
  listeners.add(listener);
  listener(state);
  return () => listeners.delete(listener);
}

async function recoverOnce(): Promise<void> {
  if (recovered) return;
  if (recovery) return recovery;
  recovery = (async () => {
    const result = await recoverGlassesM4aRecording();
    if (result.recovered && result.outputUri) {
      await finalizeNativeRecording({
        completed: true,
        reason: 'recovered',
        outputUri: result.outputUri,
      });
    }
    recovered = true;
  })().finally(() => {
    recovery = null;
  });
  return recovery;
}

export function hydrateGlassesAudioRecording(): Promise<void> {
  if (hydration) return hydration;
  if (startOperation || stopOperation) return Promise.resolve();
  subscribeNativeCompletionOnce();
  const generation = captureGeneration;
  const completionVersion = nativeCompletionVersion;
  hydration = (async () => {
    const active = await getGlassesM4aRecordingStatus();
    if (
      generation !== captureGeneration ||
      completionVersion !== nativeCompletionVersion ||
      startOperation ||
      stopOperation
    )
      return;
    if (active.recording) {
      publish({
        phase: GlassesAudioRecordingPhase.Recording,
        recording: true,
        startedAt: active.startedAt,
        outputUri: active.outputUri,
      });
    } else {
      clearCapture();
      // Recovery and provider inspection never gate screen initialization.
      void recoverOnce().catch((error) =>
        publish({
          lastError: error instanceof Error ? error.message : 'Could not recover recording.',
        }),
      );
    }
  })().finally(() => {
    hydration = null;
  });
  return hydration;
}

export function startGlassesAudioRecording(): Promise<void> {
  if (startOperation) return startOperation;
  if (stopOperation) return stopOperation.then(() => startGlassesAudioRecording());
  if (state.recording) return Promise.resolve();
  const generation = ++captureGeneration;
  publish({ phase: GlassesAudioRecordingPhase.Starting, lastError: null });
  startOperation = startRecording(generation).finally(() => {
    startOperation = null;
  });
  return startOperation;
}

async function startRecording(generation: number): Promise<void> {
  const began = Date.now();
  let outputUri: string | null = null;
  let nativeStarted = false;
  try {
    if (Platform.OS !== 'android')
      throw new Error('Glasses audio recording is currently Android-only.');
    subscribeNativeCompletionOnce();
    const [connected, folderUri] = await Promise.all([
      (async () => {
        const status = await getMentraConnectionStatus();
        return (
          (status.connected && (!status.deviceModel || status.deviceModel === 'Mentra Live')) ||
          ensureMentraConnection({ applyCaptureDefaults: false })
        );
      })(),
      getDigitalBrainStorageFolder(DigitalBrainStorageFolder.Recordings),
      micDisableInFlight,
    ]);
    debug('prepare', began);
    if (!connected)
      throw new Error('Connect a Mentra Live in Settings → Smart glasses before recording.');
    if (!folderUri) throw new Error('Choose a Digital Brain storage location before recording.');
    const date = new Date();
    // Keep any existing PCM stream on during handoff. The wake detector is
    // paused/reset, but cycling the glasses mic off/on is unnecessary.
    const prepared = await Promise.allSettled([
      FileSystem.StorageAccessFramework.createFileAsync(
        folderUri,
        recordingFileName(date),
        'audio/mp4',
      ),
      pauseWakeWordListening('audio_recording', { keepMicrophoneEnabled: true }),
      stopRecordingPlayback(),
    ]);
    if (prepared[0].status === 'fulfilled') outputUri = prepared[0].value;
    for (const item of prepared) if (item.status === 'rejected') throw item.reason;
    if (!outputUri) throw new Error('Could not create the recording file.');
    debug('file_and_mic_ready', began);
    const native = await startGlassesM4aRecording(outputUri);
    nativeStarted = true;
    publish({ outputUri, recording: true, startedAt: native.startedAt ?? date.getTime() });
    await setMentraMicState(true);
    // Bluetooth may have ended capture while the start acknowledgement was in flight.
    if (state.outputUri !== outputUri)
      throw new Error('The glasses disconnected while recording started.');
    publish({ phase: GlassesAudioRecordingPhase.Recording, recording: true });
    debug('started', began);
  } catch (error) {
    if (nativeStarted) {
      const stopped = await stopGlassesM4aRecording('start_failed').catch(() => null);
      if (stopped?.completed && stopped.outputUri) void finalizeNativeRecording(stopped);
      micDisableInFlight = setMentraMicState(false).catch(() => undefined);
    }
    clearCapture();
    // Only delete files that never reached the recorder. Native capture owns
    // cleanup once started, so a late completion cannot index a deleted file.
    if (outputUri && !nativeStarted)
      void FileSystem.deleteAsync(outputUri, { idempotent: true }).catch(() => undefined);
    resumeWakeWordAfterRecording(generation, 'audio_recording_start_failed');
    throw error;
  }
}

export function stopGlassesAudioRecording(): Promise<GlassesAudioRecordingStopResult> {
  if (stopOperation) return stopOperation;
  if (startOperation) return startOperation.then(() => stopGlassesAudioRecording());
  if (!state.recording) return Promise.resolve({ saved: Promise.resolve(null) });
  const generation = ++captureGeneration;
  publish({ phase: GlassesAudioRecordingPhase.Stopping });
  stopOperation = stopRecording(generation).finally(() => {
    stopOperation = null;
  });
  return stopOperation;
}

async function stopRecording(generation: number): Promise<GlassesAudioRecordingStopResult> {
  const began = Date.now();
  try {
    const result = await stopGlassesM4aRecording('user_stopped');
    micDisableInFlight = setMentraMicState(false).catch(() => undefined);
    clearCapture();
    debug('native_stopped', began);
    const saved = result.outputUri ? finalizeNativeRecording(result) : Promise.resolve(null);
    resumeWakeWordAfterRecording(generation, 'audio_recording_stopped');
    return { saved };
  } catch (error) {
    // A failed native stop is not evidence that recording ended. Keep Stop
    // available so the user can retry; screen focus also reconciles native state.
    publish({ phase: GlassesAudioRecordingPhase.Recording });
    throw error;
  }
}

export function listGlassesAudioRecordings(): Promise<GlassesAudioRecording[]> {
  return readRecordingLibrary();
}

function recordingDateFromFileName(name: string): string | null {
  const match = name.match(/(\d{4}-\d{2}-\d{2}T\d{2})-(\d{2})-(\d{2})-(\d{3})Z/i);
  if (!match) return null;
  const timestamp = `${match[1]}:${match[2]}:${match[3]}.${match[4]}Z`;
  return Number.isFinite(Date.parse(timestamp)) ? new Date(timestamp).toISOString() : null;
}

/** Reconcile the recordings index with the selected shared folder. */
export async function reconcileGlassesAudioRecordingLibrary(): Promise<GlassesAudioRecording[]> {
  if (recovery) await recovery.catch(() => undefined);
  if (state.phase !== GlassesAudioRecordingPhase.Idle || state.savingCount > 0) {
    return readRecordingLibrary();
  }
  const baseUri = await getDigitalBrainStorageBaseUri();
  if (!baseUri) return readRecordingLibrary();
  if (!DigitalBrainStorageNative) throw new Error('Recording folder sync needs an Android rebuild.');

  const files = (await DigitalBrainStorageNative.listSubdirectory(
    baseUri,
    DigitalBrainStorageFolder.Recordings,
  )).filter(
    (file) =>
      file.name.toLowerCase().endsWith('.m4a') || file.mimeType.toLowerCase().startsWith('audio/'),
  );
  let changed = false;
  await updateRecordingLibrary((current) => {
    const byUri = new Map(current.map((recording) => [recording.uri, recording]));
    const byName = new Map(current.map((recording) => [recording.name, recording]));
    const next = files
      .map((file) => {
        const existing = byUri.get(file.uri) ?? byName.get(file.name);
        return {
          id: existing?.id ?? `mentra-audio:${file.uri}`,
          uri: file.uri,
          name: file.name,
          startedAt:
            existing?.startedAt ?? recordingDateFromFileName(file.name) ?? new Date().toISOString(),
          durationMs: existing?.durationMs ?? 0,
          sizeBytes: file.bytes,
        };
      })
      .sort((a, b) => b.startedAt.localeCompare(a.startedAt));
    changed = JSON.stringify(current) !== JSON.stringify(next);
    return changed ? next : current;
  });
  if (changed) publish({ libraryVersion: state.libraryVersion + 1 });
  return readRecordingLibrary();
}

function withPlayback(action: () => Promise<void>): Promise<void> {
  const operation = playbackOperations.then(action);
  playbackOperations = operation.catch(() => undefined);
  return operation;
}

function stopRecordingPlayback(): Promise<void> {
  return withPlayback(async () => {
    await stopGlassesM4aPlayback();
    publish({ isPlayingUri: null });
  });
}

export async function renameGlassesAudioRecording(
  recording: GlassesAudioRecording,
  nextName: string,
): Promise<GlassesAudioRecording> {
  const baseName = nextName.trim().replace(/\.m4a$/i, '');
  if (!baseName) throw new Error('Give the recording a name.');
  if (!DigitalBrainStorageNative) throw new Error('Rename needs an Android rebuild.');
  if (state.isPlayingUri === recording.uri) await stopRecordingPlayback();
  const name = `${safeStorageFileName(baseName, 'Recording')}.m4a`;
  const renamed = await DigitalBrainStorageNative.renameDocument(recording.uri, name);
  const updated = { ...recording, uri: renamed.uri, name };
  await updateRecordingLibrary((current) =>
    current.map((item) => (item.id === recording.id ? updated : item)),
  );
  publish({ libraryVersion: state.libraryVersion + 1 });
  return updated;
}

export async function deleteGlassesAudioRecording(recording: GlassesAudioRecording): Promise<void> {
  if (state.isPlayingUri === recording.uri) await stopRecordingPlayback();
  await FileSystem.deleteAsync(recording.uri, { idempotent: true });
  await updateRecordingLibrary((current) => current.filter((item) => item.id !== recording.id));
  publish({ libraryVersion: state.libraryVersion + 1 });
}

export function playOrStopGlassesAudioRecording(recording: GlassesAudioRecording): Promise<void> {
  return withPlayback(async () => {
    if (state.isPlayingUri === recording.uri) {
      await stopGlassesM4aPlayback();
      publish({ isPlayingUri: null });
      return;
    }
    if (state.phase !== GlassesAudioRecordingPhase.Idle)
      throw new Error('Stop recording before playing audio.');
    const info = await FileSystem.getInfoAsync(recording.uri);
    if (!info.exists)
      throw new Error('This recording is no longer available in the selected folder.');
    // Publish before native preparation so a very short file's completion
    // cannot arrive first and leave a stale playing indicator.
    publish({ isPlayingUri: recording.uri });
    try {
      await playGlassesM4aRecording(recording.uri);
    } catch (error) {
      publish({ isPlayingUri: null });
      throw error;
    }
  });
}
