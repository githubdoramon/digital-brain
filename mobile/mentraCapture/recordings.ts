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

let state: GlassesAudioRecordingState = {
  recording: false,
  startedAt: null,
  outputUri: null,
  isPlayingUri: null,
  lastError: null,
};
const listeners = new Set<RecordingListener>();
let nativeCompletionSubscribed = false;

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

async function finalizeNativeRecording(result: GlassesM4aRecordingResult): Promise<void> {
  if (!result.completed) {
    publish({
      recording: false,
      startedAt: null,
      outputUri: null,
      lastError: 'The recording stopped before an audio file could be saved.',
    });
    return;
  }
  const info = await FileSystem.getInfoAsync(result.outputUri);
  if (!info.exists || !('size' in info) || !info.size) {
    publish({
      recording: false,
      startedAt: null,
      outputUri: null,
      lastError: 'The recording file could not be verified.',
    });
    return;
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
}

function subscribeNativeCompletionOnce(): void {
  if (nativeCompletionSubscribed) return;
  nativeCompletionSubscribed = true;
  subscribeGlassesM4aRecordingFinished((result) => {
    void finalizeNativeRecording(result);
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
  const connected = await ensureMentraConnection({ applyCaptureDefaults: false });
  if (!connected)
    throw new Error('Connect a Mentra Live in Settings → Smart glasses before recording.');
  const folderUri = await getDigitalBrainStorageFolder(DigitalBrainStorageFolder.Recordings);
  if (!folderUri) throw new Error('Choose a Digital Brain storage location before recording.');
  const startedAt = new Date();
  const fileName = recordingFileName(startedAt);
  const outputUri = await FileSystem.StorageAccessFramework.createFileAsync(
    folderUri,
    fileName,
    'audio/mp4',
  );
  try {
    const native = await startGlassesM4aRecording(outputUri);
    await setMentraMicState(true);
    publish({
      recording: true,
      startedAt: native.startedAt ?? startedAt.getTime(),
      outputUri,
      lastError: null,
    });
  } catch (error) {
    await stopGlassesM4aRecording('start_failed').catch(() => undefined);
    await FileSystem.deleteAsync(outputUri, { idempotent: true }).catch(() => undefined);
    throw error;
  }
}

export async function stopGlassesAudioRecording(): Promise<void> {
  if (!state.recording) return;
  const result = await stopGlassesM4aRecording('user_stopped');
  await setMentraMicState(false).catch(() => undefined);
  await finalizeNativeRecording(result);
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
