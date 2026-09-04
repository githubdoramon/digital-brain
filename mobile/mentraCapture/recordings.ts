import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import DigitalBrainStorageNative from '@/modules/digital-brain-storage/src';
import {
  DigitalBrainStorageFolder,
  getDigitalBrainStorageFolder,
} from '@/storage/digitalBrainStorage';

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
  type GlassesM4aRecordingResult,
} from './sdk';
import { pauseWakeWordListening, resumeWakeWordListening } from './wakeWord';

const RECORDINGS_STORAGE_KEY = 'digitalbrain.mentra.audio.recordings.v1';

export type GlassesAudioRecording = {
  id: string;
  uri: string;
  name: string;
  startedAt: string;
  durationMs: number;
  sizeBytes: number;
};

export type GlassesAudioRecordingState = {
  recording: boolean;
  startedAt: number | null;
  outputUri: string | null;
  isPlayingUri: string | null;
  lastError: string | null;
};

type RecordingListener = (state: GlassesAudioRecordingState) => void;

export type GlassesAudioRecordingStopResult = {
  saved: Promise<GlassesAudioRecording | null>;
};

let state: GlassesAudioRecordingState = {
  recording: false,
  startedAt: null,
  outputUri: null,
  isPlayingUri: null,
  lastError: null,
};
const listeners = new Set<RecordingListener>();
let nativeCompletionSubscribed = false;
const nativeFinalizations = new Map<string, Promise<GlassesAudioRecording | null>>();
let wakeWordResume: Promise<void> | null = null;
let micDisableInFlight: Promise<void> | null = null;

function publish(next: Partial<GlassesAudioRecordingState>): void {
  state = { ...state, ...next };
  listeners.forEach((listener) => listener(state));
}

function recordingFileName(startedAt: Date): string {
  const iso = startedAt.toISOString().replace(/[:.]/g, '-');
  return `Mentra recording ${iso}.m4a`;
}

function recordingId(uri: string): string {
  return `mentra-audio:${uri}`;
}

async function loadStoredRecordings(): Promise<GlassesAudioRecording[]> {
  const raw = await AsyncStorage.getItem(RECORDINGS_STORAGE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as GlassesAudioRecording[]) : [];
  } catch {
    return [];
  }
}

async function saveStoredRecordings(recordings: GlassesAudioRecording[]): Promise<void> {
  await AsyncStorage.setItem(RECORDINGS_STORAGE_KEY, JSON.stringify(recordings));
}

async function finalizeNativeRecordingOnce(
  result: GlassesM4aRecordingResult,
): Promise<GlassesAudioRecording | null> {
  if (!result.completed) {
    publish({
      recording: false,
      startedAt: null,
      outputUri: null,
      lastError: 'The recording stopped before an audio file could be saved.',
    });
    return null;
  }
  const info = await FileSystem.getInfoAsync(result.outputUri);
  if (!info.exists || !('size' in info) || !info.size) {
    publish({
      recording: false,
      startedAt: null,
      outputUri: null,
      lastError: 'The recording file could not be verified.',
    });
    return null;
  }
  const startedAt = new Date(result.startedAt ?? Date.now());
  const next: GlassesAudioRecording = {
    id: recordingId(result.outputUri),
    uri: result.outputUri,
    name: recordingFileName(startedAt),
    startedAt: startedAt.toISOString(),
    durationMs: result.durationMs ?? 0,
    sizeBytes: info.size,
  };
  const existing = await loadStoredRecordings();
  await saveStoredRecordings([next, ...existing.filter((item) => item.uri !== next.uri)]);
  publish({ recording: false, startedAt: null, outputUri: null, lastError: null });
  return next;
}

function finalizeNativeRecording(
  result: GlassesM4aRecordingResult,
): Promise<GlassesAudioRecording | null> {
  const existing = nativeFinalizations.get(result.outputUri);
  if (existing) return existing;
  const completion = finalizeNativeRecordingOnce(result).finally(() => {
    nativeFinalizations.delete(result.outputUri);
  });
  nativeFinalizations.set(result.outputUri, completion);
  return completion;
}

function resumeWakeWordAfterRecording(reason: string): void {
  if (wakeWordResume) return;
  wakeWordResume = resumeWakeWordListening('audio_recording', reason)
    .catch(() => undefined)
    .finally(() => {
      wakeWordResume = null;
    });
}

function subscribeNativeCompletionOnce(): void {
  if (nativeCompletionSubscribed) return;
  nativeCompletionSubscribed = true;
  subscribeGlassesM4aRecordingFinished((result) => {
    void finalizeNativeRecording(result)
      .catch(() => undefined)
      .finally(() => resumeWakeWordAfterRecording('audio_recording_finished'));
  });
}

export function getGlassesAudioRecordingState(): GlassesAudioRecordingState {
  return state;
}

export function subscribeGlassesAudioRecording(listener: RecordingListener): () => void {
  subscribeNativeCompletionOnce();
  listener(state);
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function hydrateGlassesAudioRecording(): Promise<void> {
  subscribeNativeCompletionOnce();
  const active = await getGlassesM4aRecordingStatus();
  if (active.recording) {
    publish({
      recording: true,
      startedAt: active.startedAt,
      outputUri: active.outputUri,
      lastError: null,
    });
    return;
  }
  const recovered = await recoverGlassesM4aRecording();
  if (recovered.recovered && recovered.outputUri) {
    const info = await FileSystem.getInfoAsync(recovered.outputUri);
    if (info.exists && 'size' in info && info.size) {
      const date = new Date();
      const recording: GlassesAudioRecording = {
        id: recordingId(recovered.outputUri),
        uri: recovered.outputUri,
        name: recordingFileName(date),
        startedAt: date.toISOString(),
        durationMs: 0,
        sizeBytes: info.size,
      };
      const existing = await loadStoredRecordings();
      await saveStoredRecordings([
        recording,
        ...existing.filter((item) => item.uri !== recording.uri),
      ]);
    }
  }
  publish({ recording: false, startedAt: null, outputUri: null });
}

export async function startGlassesAudioRecording(): Promise<void> {
  if (Platform.OS !== 'android')
    throw new Error('Glasses audio recording is currently Android-only.');
  if (state.recording) return;
  subscribeNativeCompletionOnce();
  if (micDisableInFlight) {
    await micDisableInFlight;
    micDisableInFlight = null;
  }
  // App startup may still be applying camera defaults through the shared
  // connection operation. Audio only needs a ready glasses link, so avoid
  // waiting for unrelated camera setup when the link is already usable.
  const [connected, folderUri] = await Promise.all([
    (async () => {
      const status = await getMentraConnectionStatus();
      const readyForAudio =
        status.connected && (!status.deviceModel || status.deviceModel === 'Mentra Live');
      return readyForAudio || ensureMentraConnection({ applyCaptureDefaults: false });
    })(),
    getDigitalBrainStorageFolder(DigitalBrainStorageFolder.Recordings),
  ]);
  if (!connected)
    throw new Error('Connect a Mentra Live in Settings → Smart glasses before recording.');
  if (!folderUri) throw new Error('Choose a Digital Brain storage location before recording.');
  const startedAt = new Date();
  const fileName = recordingFileName(startedAt);
  let outputUri: string | null = null;
  try {
    // Folder/file creation and wake-word shutdown are independent. Running
    // them together removes a full SAF round trip from the perceived start
    // latency while still waiting for both before enabling the recorder.
    const [createdUri] = await Promise.all([
      FileSystem.StorageAccessFramework.createFileAsync(folderUri, fileName, 'audio/mp4'),
      pauseWakeWordListening('audio_recording'),
    ]);
    outputUri = createdUri;
    const native = await startGlassesM4aRecording(createdUri);
    await setMentraMicState(true);
    publish({
      recording: true,
      startedAt: native.startedAt ?? startedAt.getTime(),
      outputUri,
      lastError: null,
    });
  } catch (error) {
    await stopGlassesM4aRecording('start_failed').catch(() => undefined);
    if (outputUri)
      await FileSystem.deleteAsync(outputUri, { idempotent: true }).catch(() => undefined);
    await resumeWakeWordListening('audio_recording', 'audio_recording_start_failed').catch(
      () => undefined,
    );
    throw error;
  }
}

export async function stopGlassesAudioRecording(): Promise<GlassesAudioRecordingStopResult> {
  if (!state.recording) return { saved: Promise.resolve(null) };
  let disableMic: Promise<void> | null = null;
  try {
    // Native finish takes the recorder lock first and immediately stops
    // accepting PCM. Disable the glasses mic immediately afterwards, but do
    // not make the UI wait for the BLE acknowledgement. Starting with native
    // finish also prevents its audio_disconnected completion event from
    // racing the explicit user stop and producing a false failed save.
    const result = await stopGlassesM4aRecording('user_stopped');
    disableMic = setMentraMicState(false).catch(() => undefined);
    micDisableInFlight = disableMic;
    // Native finish has closed the encoder and stopped accepting PCM. Release
    // the screen immediately; SAF metadata persistence can be slower and is
    // exposed to the caller as a separate completion promise.
    publish({ recording: false, startedAt: null, outputUri: null, lastError: null });
    const saved = finalizeNativeRecording(result);
    return { saved };
  } finally {
    if (!disableMic) {
      disableMic = setMentraMicState(false).catch(() => undefined);
      micDisableInFlight = disableMic;
    }
    // Wake-word startup can load native models and reacquire the glasses mic.
    // It is deliberately outside the user-facing stop operation; otherwise a
    // successful stop leaves the shared Button in its loading state.
    void disableMic.finally(() => resumeWakeWordAfterRecording('audio_recording_stopped'));
  }
}

export async function listGlassesAudioRecordings(): Promise<GlassesAudioRecording[]> {
  const stored = await loadStoredRecordings();
  const available = await Promise.all(
    stored.map(async (recording) => ({
      recording,
      info: await FileSystem.getInfoAsync(recording.uri).catch(() => ({ exists: false })),
    })),
  );
  const existing = available
    .filter(({ info }) => info.exists)
    .map(({ recording, info }) => ({
      ...recording,
      sizeBytes: 'size' in info && typeof info.size === 'number' ? info.size : recording.sizeBytes,
    }))
    .sort((left, right) => right.startedAt.localeCompare(left.startedAt));
  if (existing.length !== stored.length) await saveStoredRecordings(existing);
  return existing;
}

export async function renameGlassesAudioRecording(
  recording: GlassesAudioRecording,
  nextName: string,
): Promise<GlassesAudioRecording> {
  const baseName = nextName.trim().replace(/\.m4a$/i, '');
  if (!baseName) throw new Error('Give the recording a name.');
  if (!DigitalBrainStorageNative) throw new Error('Rename needs an Android rebuild.');
  const renamed = await DigitalBrainStorageNative.renameDocument(recording.uri, `${baseName}.m4a`);
  const updated = {
    ...recording,
    uri: renamed.uri,
    id: recordingId(renamed.uri),
    name: `${baseName}.m4a`,
  };
  const existing = await loadStoredRecordings();
  await saveStoredRecordings(existing.map((item) => (item.id === recording.id ? updated : item)));
  return updated;
}

export async function deleteGlassesAudioRecording(recording: GlassesAudioRecording): Promise<void> {
  await stopGlassesM4aPlayback().catch(() => undefined);
  await FileSystem.deleteAsync(recording.uri, { idempotent: true });
  const existing = await loadStoredRecordings();
  await saveStoredRecordings(existing.filter((item) => item.id !== recording.id));
  if (state.isPlayingUri === recording.uri) publish({ isPlayingUri: null });
}

export async function playOrStopGlassesAudioRecording(
  recording: GlassesAudioRecording,
): Promise<void> {
  if (state.isPlayingUri === recording.uri) {
    await stopGlassesM4aPlayback();
    publish({ isPlayingUri: null });
    return;
  }
  await playGlassesM4aRecording(recording.uri);
  publish({ isPlayingUri: recording.uri });
}
