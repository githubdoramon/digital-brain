import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

import type { CaptureQueueEntry } from './types';
import {
  copyToDigitalBrainStorage,
  DigitalBrainStorageFolder,
  getDigitalBrainStorageBaseUri,
  getDigitalBrainStorageFolder,
} from '@/storage/digitalBrainStorage';

const STORAGE_KEY = 'digitalbrain.glasses.capture.queue.v1';
const FOLDER_KEY = 'digitalbrain.glasses.capture.folder.v1';
const BASE_DIRECTORY = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}Digital Brain/Capture Queue/`;

/**
 * Expo's SAF directory enumeration returns child document URIs, while the
 * legacy SAF helpers expect a tree URI when a directory is used as a parent.
 * Passing the child URI through unchanged makes Android treat the document
 * path as relative to the original tree and can produce paths such as
 * `Documents/Digital Brain/Documents/Digital Brain/...`.
 *
 * Rebuild a canonical tree URI from the document id. This also accepts the
 * root URI returned by getUriForDirectoryInRoot(), which includes both
 * `/tree/` and `/document/` segments.
 */
export function normalizeSafDirectoryUri(uri: string): string {
  if (!uri.startsWith('content://')) return uri;

  const withoutQuery = uri.split(/[?#]/, 1)[0];
  const treeMarker = '/tree/';
  const documentMarker = '/document/';
  const treeIndex = withoutQuery.indexOf(treeMarker);
  const documentIndex = withoutQuery.indexOf(documentMarker);
  if (treeIndex < 0 && documentIndex < 0) return uri;

  const markerIndex =
    documentIndex >= 0 ? documentIndex + documentMarker.length : treeIndex + treeMarker.length;
  // Some Android providers return a malformed child URI with another
  // `/document/…` suffix appended. Only the first document token is the actual
  // document id; retaining the suffix recreates the same invalid URI on every
  // retry (and causes paths to be nested repeatedly).
  let documentId = withoutQuery.slice(markerIndex).split(documentMarker, 1)[0];
  try {
    documentId = decodeURIComponent(documentId);
  } catch {
    // Keep the original token if a provider returns a malformed escape.
  }
  documentId = documentId.replace(/^\/+|\/+$/g, '');
  // A pre-fix build could save the app-owned path twice when it passed a
  // child document URI back as a parent. Collapse that known duplicate while
  // repairing the persisted value; user media is never removed by this step.
  documentId = documentId.replace(/\/Digital Brain\/Digital Brain(?=\/|$)/g, '/Digital Brain');
  if (!documentId) return uri;

  const authorityEnd = treeIndex >= 0 ? treeIndex : documentIndex;
  const encodedDocumentId = encodeURIComponent(documentId);
  return `${withoutQuery.slice(0, authorityEnd)}${treeMarker}${encodedDocumentId}${documentMarker}${encodedDocumentId}`;
}

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
  const sharedFolder = await getDigitalBrainStorageFolder(
    DigitalBrainStorageFolder.GlassesCaptureQueue,
  );
  if (sharedFolder) return sharedFolder;
  const stored = await AsyncStorage.getItem(FOLDER_KEY);
  if (!stored) return null;
  const normalized = normalizeSafDirectoryUri(stored);
  if (normalized !== stored) {
    // Heal URIs saved by older builds so future syncs do not repeat the bad
    // parent path. A failure here should not make the queue inaccessible.
    await AsyncStorage.setItem(FOLDER_KEY, normalized).catch(() => undefined);
  }
  return normalized;
}

export async function setCaptureFolderUri(uri: string): Promise<void> {
  await AsyncStorage.setItem(FOLDER_KEY, normalizeSafDirectoryUri(uri));
}

export async function ensurePrivateCaptureDirectory(): Promise<void> {
  await FileSystem.makeDirectoryAsync(BASE_DIRECTORY, { intermediates: true });
}

export function privateCapturePath(fileName: string): string {
  return `${BASE_DIRECTORY}${fileName.replace(/[^a-zA-Z0-9._-]/g, '_')}`;
}

function mediaLeafName(fileName: string): string {
  const leaf = fileName.split('/').pop()?.trim() ?? '';
  return leaf || 'capture.bin';
}

function visibleCaptureName(entry: CaptureQueueEntry): string {
  return `${entry.captureId.replace(/[^a-zA-Z0-9._-]/g, '_')}-${mediaLeafName(entry.fileName)}`;
}

export async function deleteLocalCapture(uri: string): Promise<void> {
  await FileSystem.deleteAsync(uri, { idempotent: true });
}

export async function getLocalCaptureInfo(uri: string): Promise<{ exists: boolean; size: number }> {
  const info = await FileSystem.getInfoAsync(uri);
  return { exists: info.exists, size: info.exists && 'size' in info ? (info.size ?? 0) : 0 };
}

/**
 * Move already-downloaded queue entries from the app-private fallback into the
 * user-selected Documents/Capture Queue folder. This matters when the folder is
 * selected after a failed upload: retrying the upload must not leave those files
 * stranded in app-private storage where Android's Files app cannot show them.
 */
export async function movePendingCapturesToSharedFolder(
): Promise<{ moved: number; failed: number }> {
  if (!(await getDigitalBrainStorageBaseUri())) return { moved: 0, failed: 0 };
  let queue = await loadCaptureQueue();
  let moved = 0;
  let failed = 0;

  for (const entry of queue) {
    if (!entry.localUri || entry.state === 'missing' || entry.state === 'uploaded') continue;
    // A previous successful migration already points at the Documents copy.
    if (entry.localUri.startsWith('content://')) continue;
    const source = entry.localUri;
    const sourceInfo = await getLocalCaptureInfo(source);
    if (!sourceInfo.exists || sourceInfo.size <= 0) continue;

    const name = visibleCaptureName(entry);
    try {
      const target = await copyToDigitalBrainStorage(
        source,
        DigitalBrainStorageFolder.GlassesCaptureQueue,
        name,
        entry.mimeType,
        { skipIfSameSize: true },
      );
      await deleteLocalCapture(source);
      queue = queue.map((item) =>
        item.captureId === entry.captureId && item.fileName === entry.fileName
          ? { ...item, localUri: target, updatedAt: new Date().toISOString() }
          : item,
      );
      moved += 1;
    } catch {
      failed += 1;
    }
  }

  await saveCaptureQueue(queue);
  return { moved, failed };
}
