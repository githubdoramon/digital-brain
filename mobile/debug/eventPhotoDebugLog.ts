import * as FileSystem from 'expo-file-system/legacy';

const EVENT_PHOTO_DEBUG_LOG_FILE_NAME = 'event-photo-debug-log.txt';
const LOG_FILE_URI = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}${EVENT_PHOTO_DEBUG_LOG_FILE_NAME}`;

function serializePayload(payload: unknown): string {
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

export async function appendEventPhotoDebugLog(label: string, payload: unknown): Promise<void> {
  const timestamp = new Date().toISOString();
  const nextEntry = [`[${timestamp}] ${label}`, serializePayload(payload), '', ''].join('\n');
  await FileSystem.writeAsStringAsync(LOG_FILE_URI, nextEntry, {
    encoding: FileSystem.EncodingType.UTF8,
    append: true,
  });
}

export async function getEventPhotoDebugLogInfo(): Promise<{ exists: boolean; sizeBytes: number }> {
  const info = await FileSystem.getInfoAsync(LOG_FILE_URI);
  return {
    exists: info.exists,
    sizeBytes: info.exists && typeof info.size === 'number' ? info.size : 0,
  };
}

export async function readEventPhotoDebugLog(): Promise<string> {
  const info = await FileSystem.getInfoAsync(LOG_FILE_URI);
  if (!info.exists) {
    return '';
  }
  return FileSystem.readAsStringAsync(LOG_FILE_URI, {
    encoding: FileSystem.EncodingType.UTF8,
  });
}

export async function clearEventPhotoDebugLog(): Promise<void> {
  const info = await FileSystem.getInfoAsync(LOG_FILE_URI);
  if (info.exists) {
    await FileSystem.deleteAsync(LOG_FILE_URI, { idempotent: true });
  }
}

export function getEventPhotoDebugLogFileName(timestamp = new Date()): string {
  return `digital-brain-event-photo-debug-${timestamp.toISOString().replace(/[:.]/g, '-')}.txt`;
}
