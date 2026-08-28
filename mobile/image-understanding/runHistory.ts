import AsyncStorage from '@react-native-async-storage/async-storage';

import type { ImageUnderstandingRunRecord } from './types';

const STORAGE_KEY = 'image_understanding_poc_run_history_v1';
export const MAX_IMAGE_UNDERSTANDING_RUNS = 20;

function isRunRecord(value: unknown): value is ImageUnderstandingRunRecord {
  return (
    Boolean(value) &&
    typeof value === 'object' &&
    typeof (value as { id?: unknown }).id === 'string'
  );
}

export async function readImageUnderstandingRunHistory(): Promise<ImageUnderstandingRunRecord[]> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter(isRunRecord).slice(0, MAX_IMAGE_UNDERSTANDING_RUNS)
      : [];
  } catch {
    return [];
  }
}

export async function appendImageUnderstandingRun(
  run: ImageUnderstandingRunRecord,
): Promise<ImageUnderstandingRunRecord[]> {
  const current = await readImageUnderstandingRunHistory();
  const next = [run, ...current.filter((item) => item.id !== run.id)].slice(
    0,
    MAX_IMAGE_UNDERSTANDING_RUNS,
  );
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export async function clearImageUnderstandingRunHistory(): Promise<void> {
  await AsyncStorage.removeItem(STORAGE_KEY);
}

export function serializeImageUnderstandingRuns(runs: ImageUnderstandingRunRecord[]): string {
  return JSON.stringify(
    {
      exportVersion: 5,
      exportedAt: new Date().toISOString(),
      privacyNote:
        'Selected photos, local paths, EXIF metadata, account identifiers, and auth data are not included.',
      runs,
    },
    null,
    2,
  );
}
