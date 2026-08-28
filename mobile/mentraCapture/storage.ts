import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

import type { CaptureQueueEntry } from './types';

const STORAGE_KEY = 'digitalbrain.glasses.capture.queue.v1';
const FOLDER_KEY = 'digitalbrain.glasses.capture.folder.v1';
const BASE_DIRECTORY = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}Digital Brain/Capture Queue/`;

export async function loadCaptureQueue(): Promise<CaptureQueueEntry[]> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as CaptureQueueEntry[]) : [];
  } catch {
    return [];
  }
}

export async function saveCaptureQueue(queue: CaptureQueueEntry[]): Promise<void> {
  // Do not trim this queue: a capture remains recoverable until the backend
  // confirms its Immich asset. Terminal `missing` entries are retained as an
  // audit marker rather than silently dropping older pending media.
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
}

export async function getCaptureFolderUri(): Promise<string | null> {
  return AsyncStorage.getItem(FOLDER_KEY);
}

export async function setCaptureFolderUri(uri: string): Promise<void> {
  await AsyncStorage.setItem(FOLDER_KEY, uri);
}

export async function ensurePrivateCaptureDirectory(): Promise<void> {
  await FileSystem.makeDirectoryAsync(BASE_DIRECTORY, { intermediates: true });
}

export function privateCapturePath(fileName: string): string {
  return `${BASE_DIRECTORY}${fileName.replace(/[^a-zA-Z0-9._-]/g, '_')}`;
}

export async function deleteLocalCapture(uri: string): Promise<void> {
  await FileSystem.deleteAsync(uri, { idempotent: true });
}

export async function getLocalCaptureInfo(uri: string): Promise<{ exists: boolean; size: number }> {
  const info = await FileSystem.getInfoAsync(uri);
  return { exists: info.exists, size: info.exists && 'size' in info ? (info.size ?? 0) : 0 };
}
