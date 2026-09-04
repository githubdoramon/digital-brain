import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import { API_BASE_URL, apiFetch, getAuthRequestContext } from '@/api/client';
import { getStoredGoogleIdToken, refreshStoredGoogleIdToken } from '@/auth/backgroundToken';
import { reportLocationDebugEvent } from '@/location/debugState';

import { appendMentraDebugLog } from './debug';
import {
  disableGlassesHotspot,
  downloadGlassesFile,
  enableGlassesHotspot,
  ensureMentraConnection,
  fetchGlassesUrl,
  getKnownGlassesIp,
  getKnownHotspot,
  releaseGlassesNetwork,
} from './sdk';
import {
  copyToDigitalBrainStorage,
  DigitalBrainStorageFolder,
  getDigitalBrainStorageBaseUri,
} from '@/storage/digitalBrainStorage';
import {
  deleteLocalCapture,
  ensurePrivateCaptureDirectory,
  getLocalCaptureInfo,
  importVisibleCaptureQueueEntries,
  loadCaptureQueue,
  movePendingCapturesToSharedFolder,
  privateCapturePath,
  saveCaptureQueue,
} from './storage';
import type { CaptureQueueEntry, CaptureSyncStatus, RemoteCapture } from './types';
import { resolveCaptureLocation } from './location';
import DigitalBrainStorageNative from '@/modules/digital-brain-storage/src';

type SyncListener = (status: CaptureSyncStatus) => void;
let status: CaptureSyncStatus = {
  running: false,
  lastRunAt: null,
  lastError: null,
  pendingCount: 0,
  failedCount: 0,
  uploadedCount: 0,
  currentCaptureId: null,
  networkPath: null,
};
const listeners = new Set<SyncListener>();
let activeSync: Promise<void> | null = null;
let hotspotOpenedBySync = false;
// Every capture uses the same bounded transport. This deliberately avoids
// guessing which intermediate proxy limit applies to a specific deployment.
// The resumable session endpoint keeps every request well below the upstream
// body limit. Eight MiB reduces per-chunk HTTP overhead without reintroducing
// the old monolithic-upload failure mode.
const DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
const GLASSES_CONNECTION_TIMEOUT_MS = 30_000;

function withTimeout<T>(operation: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    operation.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function publish(next: Partial<CaptureSyncStatus>): void {
  status = { ...status, ...next };
  listeners.forEach((listener) => listener(status));
}

function debugCaptureStage(
  eventName: string,
  message: string,
  payload?: Record<string, unknown>,
): void {
  // Keep diagnostics free of media paths, URLs, auth headers, and raw bytes.
  reportLocationDebugEvent(eventName, { message, payload, recordInHistory: true });
  void appendMentraDebugLog(eventName, { message, payload }).catch(() => undefined);
}

function safeCaptureErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/\b413\b[\s\S]{0,80}payload too large|payload too large[\s\S]{0,80}\b413\b/i.test(message)) {
    return 'Upload rejected because this video exceeds the upstream size limit. It remains retained locally and other captures can continue syncing.';
  }
  // Android SAF exceptions often include the complete content URI. Keep the
  // status actionable without surfacing provider paths in the UI/diagnostics.
  return message.replace(/content:\/\/\S+/g, '[selected-folder-access]').slice(0, 240);
}

export function getCaptureSyncStatus(): CaptureSyncStatus {
  return { ...status };
}

export function subscribeCaptureSync(listener: SyncListener): () => void {
  listeners.add(listener);
  listener(getCaptureSyncStatus());
  return () => listeners.delete(listener);
}

function normalizeRemoteCaptures(payload: any, baseUrl: string): RemoteCapture[] {
  const data = payload?.data ?? payload;
  const groups = Array.isArray(data?.captures) ? data.captures : [];
  if (groups.length > 0) {
    return groups.flatMap((group: any) =>
      (Array.isArray(group.files) ? group.files : [])
        // The gallery groups HDR brackets and IMU sidecars with the primary
        // media. Upload only the user-facing original; sidecars/brackets are
        // not standalone captures for Immich.
        .filter((file: any) => !file?.role || file.role === 'primary')
        .map((file: any) => ({
          captureId: String(group.capture_id ?? file.name),
          kind: group.type === 'video' ? 'video' : 'photo',
          capturedAt:
            typeof group.timestamp === 'number' ? new Date(group.timestamp).toISOString() : null,
          fileName: String(file.name),
          downloadUrl: file.download_url
            ? String(file.download_url).startsWith('http')
              ? String(file.download_url)
              : `${baseUrl}${String(file.download_url).startsWith('/') ? '' : '/'}${String(file.download_url)}`
            : `${baseUrl}/api/download?file=${encodeURIComponent(file.name)}`,
          mimeType: String(file.mime_type ?? (group.type === 'video' ? 'video/mp4' : 'image/jpeg')),
          sizeBytes: Number.isFinite(Number(file.size)) ? Number(file.size) : null,
          // The current v3 server accepts a client-generated idempotency key. Keep it
          // deterministic so a retry after a process death is safe even though older
          // manifests do not echo an ack_id field.
          ackId: group.ack_id
            ? String(group.ack_id)
            : group.ackId
              ? String(group.ackId)
              : `digitalbrain-${String(group.capture_id ?? file.name)}`,
          protocolVersion: 3,
        })),
    );
  }
  const photos = Array.isArray(data?.photos) ? data.photos : [];
  return photos.map((photo: any) => ({
    captureId: String(photo.capture_id ?? photo.name),
    kind: String(photo.mime_type ?? '').startsWith('video/') ? 'video' : 'photo',
    capturedAt:
      typeof photo.timestamp === 'number' ? new Date(photo.timestamp).toISOString() : null,
    fileName: String(photo.name),
    downloadUrl: `${baseUrl}/api/download?file=${encodeURIComponent(String(photo.name))}`,
    mimeType: String(photo.mime_type ?? 'image/jpeg'),
    sizeBytes: Number.isFinite(Number(photo.size)) ? Number(photo.size) : null,
    ackId: null,
    protocolVersion: 1,
  }));
}

function mediaLeafName(fileName: string): string {
  const leaf = fileName.split('/').pop()?.trim() ?? '';
  return leaf || 'capture.bin';
}

async function discoverAllCaptures(baseUrl: string): Promise<RemoteCapture[]> {
  const first = await fetchGlassesUrl(`${baseUrl}/api/v3/manifest?limit=100`);
  if (!first.ok) {
    const captures: RemoteCapture[] = [];
    let offset = 0;
    while (true) {
      const legacy = await fetchGlassesUrl(`${baseUrl}/api/gallery?limit=100&offset=${offset}`);
      if (!legacy.ok) throw new Error(`Glasses gallery unavailable (${legacy.status})`);
      const payload = await legacy.json();
      captures.push(...normalizeRemoteCaptures(payload, baseUrl));
      const data = payload?.data ?? payload;
      if (!data?.has_more) break;
      const pageCount = Array.isArray(data?.photos) ? data.photos.length : 0;
      if (pageCount === 0) break;
      offset += pageCount;
    }
    return captures;
  }

  const captures: RemoteCapture[] = [];
  let response = first;
  let cursor: string | null = null;
  const seenCursors = new Set<string>();
  do {
    const payload = await response.json();
    captures.push(...normalizeRemoteCaptures(payload, baseUrl));
    const data = payload?.data ?? payload;
    const next = typeof data?.next_cursor === 'string' ? data.next_cursor : null;
    if (!data?.has_more || !next || seenCursors.has(next)) break;
    seenCursors.add(next);
    cursor = next;
    const nextCursor = next;
    response = await fetchGlassesUrl(
      `${baseUrl}/api/v3/manifest?limit=100&cursor=${encodeURIComponent(nextCursor)}`,
    );
    if (!response.ok) throw new Error(`Glasses gallery page unavailable (${response.status})`);
  } while (cursor);
  return captures;
}

async function probeGalleryHealth(url: string): Promise<Response> {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), 5_000) : null;
  try {
    return await fetchGlassesUrl(url, controller ? { signal: controller.signal } : undefined);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

async function connectGalleryServer(): Promise<{
  baseUrl: string;
  path: 'current_wifi' | 'glasses_hotspot';
  closeHotspot: boolean;
}> {
  const candidates = [getKnownGlassesIp(), getKnownHotspot()?.localIp].filter(Boolean) as string[];
  for (const ip of candidates) {
    const baseUrl = `http://${ip}:8089`;
    try {
      const response = await probeGalleryHealth(`${baseUrl}/api/health`);
      if (response.ok)
        return {
          baseUrl,
          path: ip === getKnownGlassesIp() ? 'current_wifi' : 'glasses_hotspot',
          closeHotspot: false,
        };
    } catch {
      // Try the next transport.
    }
  }
  const hotspot = await enableGlassesHotspot();
  hotspotOpenedBySync = hotspot.openedByUs;
  const baseUrl = `http://${hotspot.localIp}:8089`;
  const response = await probeGalleryHealth(`${baseUrl}/api/health`);
  if (!response.ok) throw new Error(`Glasses camera server unavailable (${response.status})`);
  return { baseUrl, path: 'glasses_hotspot', closeHotspot: hotspot.openedByUs };
}

async function downloadToPhone(remote: RemoteCapture): Promise<string> {
  await ensurePrivateCaptureDirectory();
  const temporary = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${remote.captureId}.download`;
  await FileSystem.deleteAsync(temporary, { idempotent: true });
  // Use the SDK's streaming bridge for hotspot transfers. Reading the response into a Buffer
  // would expand a long video to base64 in JS memory and can crash an Android device.
  await downloadGlassesFile(remote.downloadUrl, temporary);
  const downloaded = await getLocalCaptureInfo(temporary);
  if (!downloaded.exists || downloaded.size <= 0) throw new Error('Downloaded media is empty');
  if (remote.sizeBytes != null && downloaded.size !== remote.sizeBytes) {
    throw new Error(`Downloaded media size mismatch (${downloaded.size}/${remote.sizeBytes})`);
  }
  // First make the local copy durable in app storage. The shared Documents
  // copy is for visibility and recovery, but a document-provider failure must
  // never leave this capture only in a transient cache file.
  const privateTarget = privateCapturePath(remote.fileName);
  await FileSystem.deleteAsync(privateTarget, { idempotent: true });
  await FileSystem.moveAsync({ from: temporary, to: privateTarget });

  if (await getDigitalBrainStorageBaseUri()) {
    try {
      const visibleName = `${remote.captureId.replace(/[^a-zA-Z0-9._-]/g, '_')}-${mediaLeafName(remote.fileName)}`;
      const visibleTarget = await copyToDigitalBrainStorage(
        privateTarget,
        DigitalBrainStorageFolder.GlassesCaptureQueue,
        visibleName,
        remote.mimeType,
        { skipIfSameSize: true },
      );
      // Keep the app-private file as the canonical upload source. The visible
      // Documents copy is deliberately a mirror, because SAF content URIs are
      // not interchangeable with Expo's file URI APIs across app lifecycles.
      void visibleTarget;
      return privateTarget;
    } catch {
      debugCaptureStage(
        'glasses_capture_visible_copy_unavailable',
        'Documents-folder copy unavailable; retaining the capture in the app-private queue.',
      );
    }
  }
  return privateTarget;
}

async function acknowledgeCapture(baseUrl: string, entry: CaptureQueueEntry): Promise<void> {
  if (entry.protocolVersion >= 3 && entry.ackId) {
    const response = await fetchGlassesUrl(`${baseUrl}/api/v3/ack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capture_id: entry.captureId, ack_id: entry.ackId }),
    });
    if (response.ok) {
      const data = await response.json();
      const result = data?.data ?? data;
      if (!result?.success && !result?.already_trashed)
        throw new Error('Glasses did not acknowledge capture');
      return;
    }
    // A v3 manifest can outlive an older camera-server implementation. Only
    // downgrade for an unsupported route; real acknowledgement failures must
    // keep the durable queue entry for retry.
    if (response.status !== 404 && response.status !== 405) {
      throw new Error(`Glasses acknowledgement failed (${response.status})`);
    }
  }
  const response = await fetchGlassesUrl(`${baseUrl}/api/delete-files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files: [entry.fileName] }),
  });
  if (!response.ok) throw new Error(`Glasses deletion failed (${response.status})`);
  const result = await response.json().catch(() => null);
  const results = result?.results ?? result?.data?.results;
  if (Array.isArray(results)) {
    const target = results.find((item: any) => item?.file === entry.fileName);
    if (!target || (!target.success && !target.already_trashed)) {
      throw new Error('Glasses did not delete capture');
    }
  } else {
    const deleted = result?.deleted ?? result?.data?.deleted;
    if (Array.isArray(deleted) && !deleted.includes(entry.fileName)) {
      throw new Error('Glasses did not delete capture');
    }
  }
}

async function uploadCapture(entry: CaptureQueueEntry): Promise<string> {
  if (!entry.localUri) throw new Error('Local media path is missing');
  const info = await getLocalCaptureInfo(entry.localUri);
  if (!info.exists) throw new Error('Local capture was manually removed');
  // AuthProvider is not mounted in an Expo headless task. Prefer its live token, then use the
  // same SecureStore-backed token/refresh path as the existing background location drain.
  const authContext = await getAuthRequestContext();
  let token = authContext.token ?? (await getStoredGoogleIdToken());
  if (!token) {
    debugCaptureStage(
      'glasses_capture_auth_missing',
      'Capture upload skipped because authentication is unavailable.',
      {
        capture_id: entry.captureId,
      },
    );
    throw new Error('Authentication is not available for capture upload');
  }
  const location =
    entry.location ??
    (await resolveCaptureLocation(entry.capturedAt, token, async () => {
      token = await refreshStoredGoogleIdToken();
      return token;
    }));
  if (location) {
    debugCaptureStage(
      'glasses_capture_location_resolved',
      'Selected the nearest eligible phone location sample for the capture timestamp.',
      {
        capture_id: entry.captureId,
        offset_ms: location.offset_ms,
        sample_source: location.sample_source ?? 'unknown',
      },
    );
  } else {
    debugCaptureStage(
      'glasses_capture_location_unavailable',
      'No eligible phone location sample was available; continuing without coordinates.',
      { capture_id: entry.captureId },
    );
  }
  return uploadCaptureInChunks(entry, info.size, location, token, async () => {
    token = await refreshStoredGoogleIdToken();
    return token;
  });
}

type UploadLocation = NonNullable<CaptureQueueEntry['location']> | null;

function uploadSessionUrl(sessionId: string, suffix = ''): string {
  return `${API_BASE_URL.replace(/\/$/, '')}/mobile/glasses/captures/upload-sessions/${encodeURIComponent(sessionId)}${suffix}`;
}

async function uploadCaptureInChunks(
  entry: CaptureQueueEntry,
  sizeBytes: number,
  location: UploadLocation,
  initialToken: string,
  refreshToken: () => Promise<string | null>,
): Promise<string> {
  if (!entry.localUri) throw new Error('Local media path is missing');
  if (!DigitalBrainStorageNative) {
    throw new Error('Capture upload needs an Android rebuild.');
  }
  const nativeStorage = DigitalBrainStorageNative;
  let token = initialToken;
  const session = (await apiFetch('/mobile/glasses/captures/upload-sessions', {
    method: 'POST',
    body: JSON.stringify({
      capture_id: entry.captureId,
      filename: mediaLeafName(entry.fileName),
      mime_type: entry.mimeType,
      captured_at: entry.capturedAt,
      location: location ?? {},
      size_bytes: sizeBytes,
    }),
    token,
    onAuthExpired: async () => {
      const refreshed = await refreshToken();
      if (refreshed) token = refreshed;
      return refreshed;
    },
  })) as { session_id?: string; chunk_size_bytes?: number };
  const sessionId = String(session.session_id ?? '');
  if (!sessionId) throw new Error('Backend did not create an upload session');
  const chunkSize = Math.min(
    DEFAULT_UPLOAD_CHUNK_BYTES,
    Number(session.chunk_size_bytes) || DEFAULT_UPLOAD_CHUNK_BYTES,
  );

  debugCaptureStage(
    'glasses_capture_chunked_upload_started',
    'Uploading a capture to the backend in bounded ranges.',
    { capture_id: entry.captureId, size_bytes: sizeBytes, chunk_size_bytes: chunkSize },
  );
  for (let offset = 0; offset < sizeBytes; offset += chunkSize) {
    const length = Math.min(chunkSize, sizeBytes - offset);
    const send = async (): Promise<{ status: number; body: string }> =>
      nativeStorage.uploadFileRange(
        uploadSessionUrl(sessionId, '/chunk'),
        entry.localUri!,
        offset,
        length,
        {
          Authorization: `Bearer ${token}`,
          'X-Upload-Offset': String(offset),
          'X-Upload-Total': String(sizeBytes),
        },
      );
    let result = await send();
    if (result.status === 401) {
      const refreshed = await refreshToken();
      if (!refreshed) throw new Error('Authentication expired during capture upload');
      token = refreshed;
      result = await send();
    }
    if (result.status < 200 || result.status >= 300) {
      throw new Error(
        `Upload range ${offset}-${offset + length} failed (${result.status}): ${result.body.slice(0, 160)}`,
      );
    }
    debugCaptureStage(
      'glasses_capture_chunked_upload_progress',
      'Uploaded a bounded capture range to the backend.',
      { capture_id: entry.captureId, uploaded_bytes: offset + length, size_bytes: sizeBytes },
    );
  }
  const response = (await apiFetch(`/mobile/glasses/captures/upload-sessions/${encodeURIComponent(sessionId)}/complete`, {
    method: 'POST',
    token,
    onAuthExpired: async () => {
      const refreshed = await refreshToken();
      if (refreshed) token = refreshed;
      return refreshed;
    },
  })) as any;
  return String(response?.capture?.immich_asset_id ?? '');
}

/**
 * Uploads media that has already crossed the glasses → phone durability
 * boundary. This is deliberately independent of BLE, the camera server, and
 * the glasses hotspot: a local, acknowledged file must never wait for those
 * transports before it can reach the backend.
 */
async function drainLocalCaptureUploads(): Promise<void> {
  const initialQueue = await loadCaptureQueue();
  const eligible = initialQueue.filter(
    (entry) =>
      entry.uploadReady === true &&
      Boolean(entry.localUri) &&
      !['uploaded', 'missing'].includes(entry.state) &&
      (!entry.nextRetryAt || Date.parse(entry.nextRetryAt) <= Date.now()),
  );
  debugCaptureStage('glasses_capture_local_upload_drain_started', 'Draining retained local captures.', {
    eligible_count: eligible.length,
  });
  for (const initial of eligible) {
    let entry = (await loadCaptureQueue()).find(
      (item) => item.captureId === initial.captureId && item.fileName === initial.fileName,
    );
    if (!entry?.localUri || entry.uploadReady !== true) continue;
    const localUri = entry.localUri;
    try {
      const local = await getLocalCaptureInfo(localUri);
      if (!local.exists || local.size <= 0) throw new Error('Local capture was manually removed');
      entry = { ...entry, state: 'uploading', updatedAt: new Date().toISOString() };
      await saveCaptureQueue(
        (await loadCaptureQueue()).map((item) =>
          item.captureId === entry!.captureId && item.fileName === entry!.fileName ? entry! : item,
        ),
      );
      debugCaptureStage('glasses_capture_backend_upload_started', 'Uploading retained local capture.', {
        capture_id: entry.captureId,
        kind: entry.kind,
      });
      const assetId = await uploadCapture(entry);
      if (!assetId) throw new Error('Backend did not confirm an Immich asset');
      await deleteLocalCapture(localUri);
      await saveCaptureQueue(
        (await loadCaptureQueue()).filter(
          (item) => item.captureId !== entry!.captureId || item.fileName !== entry!.fileName,
        ),
      );
      debugCaptureStage('glasses_capture_backend_upload_confirmed', 'Backend confirmed retained local capture.', {
        capture_id: entry.captureId,
        immich_asset_id: assetId,
      });
    } catch (error) {
      const message = safeCaptureErrorMessage(error);
      const attempts = entry.attempts + 1;
      await saveCaptureQueue(
        (await loadCaptureQueue()).map((item) =>
          item.captureId === entry!.captureId && item.fileName === entry!.fileName
            ? {
                ...entry!,
                state: message.includes('manually removed') ? 'missing' : 'failed',
                attempts,
                nextRetryAt: new Date(Date.now() + 15_000 * 2 ** Math.min(attempts, 8)).toISOString(),
                lastError: message.slice(0, 240),
                updatedAt: new Date().toISOString(),
              }
            : item,
        ),
      );
      debugCaptureStage('glasses_capture_local_upload_failed', 'Retained local capture upload failed.', {
        capture_id: entry.captureId,
        error: message,
      });
    }
  }
}

async function runSync(): Promise<void> {
  debugCaptureStage('glasses_capture_sync_starting', 'Starting glasses capture reconciliation.');
  if (Platform.OS !== 'android')
    throw new Error('Glasses capture sync is Android-only in this release');
  const migration = await movePendingCapturesToSharedFolder();
  if (migration.moved || migration.failed) {
    debugCaptureStage(
      'glasses_capture_pending_media_migrated',
      'Reconciled pending local captures with the Documents capture queue.',
      { moved: migration.moved, failed: migration.failed },
    );
  }
  const imported = await importVisibleCaptureQueueEntries();
  if (imported) {
    debugCaptureStage('glasses_capture_visible_queue_imported', 'Imported retained Documents media into the upload queue.', {
      imported_count: imported,
    });
  }
  await drainLocalCaptureUploads();
  // A capture signal can arrive while the glasses camera is still busy. Reconnect/readiness is
  // safe here, but replaying gallery/photo/video settings during that camera transaction can
  // race the firmware and make a physical-button capture appear to do nothing. Defaults are
  // applied on pairing, app startup, and the explicit settings action instead.
  let connection: Awaited<ReturnType<typeof connectGalleryServer>> | null = null;
  let connectionError: string | null = null;
  try {
    const connected = await withTimeout(
      ensureMentraConnection({ applyCaptureDefaults: false }),
      GLASSES_CONNECTION_TIMEOUT_MS,
      'Glasses connection timed out; continuing with retained local captures.',
    );
    if (!connected) {
      throw new Error(
        'No Mentra Live is paired. Open Settings → Glasses capture and pair the glasses.',
      );
    }
    connection = await withTimeout(
      connectGalleryServer(),
      GLASSES_CONNECTION_TIMEOUT_MS,
      'Glasses gallery connection timed out; continuing with retained local captures.',
    );
    publish({ networkPath: connection.path });
    debugCaptureStage(
      'glasses_capture_network_ready',
      'Glasses camera server transport is ready.',
      {
        network_path: connection.path,
      },
    );
  } catch (error) {
    // A local file whose glasses acknowledgement already succeeded can still
    // be uploaded while Bluetooth or the glasses hotspot is temporarily down.
    // Keep reconciliation useful for that durable stage instead of blocking on
    // a fresh gallery connection.
    connectionError = safeCaptureErrorMessage(error);
    publish({ networkPath: 'unavailable', lastError: connectionError });
    debugCaptureStage(
      'glasses_capture_connection_unavailable',
      'Glasses connection unavailable; draining previously acknowledged local captures only.',
      { error: connectionError },
    );
  }
  let discovered: RemoteCapture[] = [];
  if (connection) {
    try {
      discovered = await discoverAllCaptures(connection.baseUrl);
    } catch (error) {
      debugCaptureStage(
        'glasses_capture_discovery_unavailable',
        'Glasses gallery discovery failed; continuing with the durable local queue.',
        { error: safeCaptureErrorMessage(error) },
      );
    }
  }
  if (connection) {
    debugCaptureStage(
      'glasses_capture_discovered',
      'Glasses gallery reconciliation discovered captures.',
      {
        count: discovered.length,
        network_path: connection.path,
      },
    );
  }
  let queue = await loadCaptureQueue();
  debugCaptureStage(
    'glasses_capture_queue_loaded',
    'Loaded the durable glasses capture queue.',
    {
      total: queue.length,
      failed: queue.filter((entry) => entry.state === 'failed').length,
      upload_ready: queue.filter((entry) => entry.uploadReady === true).length,
      local_ready: queue.filter((entry) => Boolean(entry.localUri)).length,
    },
  );
  const now = new Date().toISOString();
  for (const remote of discovered) {
    if (
      !queue.some(
        (entry) => entry.captureId === remote.captureId && entry.fileName === remote.fileName,
      )
    ) {
      queue.push({
        ...remote,
        state: 'discovered',
        localUri: null,
        attempts: 0,
        nextRetryAt: null,
        lastError: null,
        discoveredAt: now,
        updatedAt: now,
        immichAssetId: null,
        location: null,
      });
    }
  }
  await saveCaptureQueue(queue);
  for (const initial of queue
    .slice()
    .sort((a, b) => a.discoveredAt.localeCompare(b.discoveredAt))) {
    if (initial.state === 'missing' || (initial.state === 'uploaded' && !initial.localUri))
      continue;
    if (initial.nextRetryAt && Date.parse(initial.nextRetryAt) > Date.now()) continue;
    let entry = (await loadCaptureQueue()).find(
      (item) => item.captureId === initial.captureId && item.fileName === initial.fileName,
    );
    if (!entry) continue;
    publish({ currentCaptureId: entry.captureId });
    try {
      // A previous run may have received backend confirmation but been
      // interrupted while deleting the local copy. Finish that cleanup without
      // re-uploading the media.
      if (entry.state === 'uploaded' && entry.localUri) {
        await deleteLocalCapture(entry.localUri);
        await saveCaptureQueue(
          (await loadCaptureQueue()).filter(
            (item) => item.captureId !== entry!.captureId || item.fileName !== entry!.fileName,
          ),
        );
        continue;
      }
      if (!entry.localUri && !connection) continue;
      if (!connection && entry.uploadReady !== true) continue;
      if (!entry.localUri) {
        debugCaptureStage('glasses_capture_transfer_started', 'Downloading capture from glasses.', {
          capture_id: entry.captureId,
          kind: entry.kind,
        });
        entry = { ...entry, state: 'downloading', updatedAt: new Date().toISOString() };
        queue = (await loadCaptureQueue()).map((item) =>
          item.captureId === entry!.captureId && item.fileName === entry!.fileName ? entry! : item,
        );
        await saveCaptureQueue(queue);
        entry = {
          ...entry,
          localUri: await downloadToPhone(entry),
          state: 'local_ready',
          updatedAt: new Date().toISOString(),
          lastError: null,
        };
        const localInfo = entry.localUri ? await getLocalCaptureInfo(entry.localUri) : null;
        debugCaptureStage('glasses_capture_transfer_validated', 'Local capture copy validated.', {
          capture_id: entry.captureId,
          size_bytes: localInfo?.size ?? null,
        });
        await saveCaptureQueue(
          (await loadCaptureQueue()).map((item) =>
            item.captureId === entry!.captureId && item.fileName === entry!.fileName
              ? entry!
              : item,
          ),
        );
      }
      if (!entry.uploadReady) {
        if (!connection) continue;
        await acknowledgeCapture(connection.baseUrl, entry);
        debugCaptureStage(
          'glasses_capture_glasses_acknowledged',
          'Glasses capture acknowledged after local commit.',
          { capture_id: entry.captureId, protocol_version: entry.protocolVersion },
        );
        entry = {
          ...entry,
          state: 'glasses_acked',
          uploadReady: true,
          updatedAt: new Date().toISOString(),
        };
        await saveCaptureQueue(
          (await loadCaptureQueue()).map((item) =>
            item.captureId === entry!.captureId && item.fileName === entry!.fileName
              ? entry!
              : item,
          ),
        );
      }
      entry = { ...entry, state: 'uploading', updatedAt: new Date().toISOString() };
      await saveCaptureQueue(
        (await loadCaptureQueue()).map((item) =>
          item.captureId === entry!.captureId && item.fileName === entry!.fileName ? entry! : item,
        ),
      );
      debugCaptureStage(
        'glasses_capture_backend_upload_started',
        'Uploading local capture through the backend proxy.',
        { capture_id: entry.captureId, kind: entry.kind },
      );
      const assetId = await uploadCapture(entry);
      if (!assetId) throw new Error('Backend did not confirm an Immich asset');
      debugCaptureStage(
        'glasses_capture_backend_upload_confirmed',
        'Backend confirmed the Immich asset and capture record.',
        { capture_id: entry.captureId, immich_asset_id: assetId },
      );
      entry = {
        ...entry,
        state: 'uploaded',
        immichAssetId: assetId,
        updatedAt: new Date().toISOString(),
        lastError: null,
      };
      await saveCaptureQueue(
        (await loadCaptureQueue()).map((item) =>
          item.captureId === entry!.captureId && item.fileName === entry!.fileName ? entry! : item,
        ),
      );
      if (entry.localUri) await deleteLocalCapture(entry.localUri);
      debugCaptureStage(
        'glasses_capture_phone_cleanup',
        'Phone capture deleted after Immich confirmation.',
        { capture_id: entry.captureId, immich_asset_id: assetId },
      );
      await saveCaptureQueue(
        (await loadCaptureQueue()).filter(
          (item) => item.captureId !== entry!.captureId || item.fileName !== entry!.fileName,
        ),
      );
    } catch (error) {
      const message = safeCaptureErrorMessage(error);
      if (entry.state === 'uploaded') {
        await saveCaptureQueue(
          (await loadCaptureQueue()).map((item) =>
            item.captureId === entry!.captureId && item.fileName === entry!.fileName
              ? {
                  ...entry!,
                  nextRetryAt: new Date(Date.now() + 60_000).toISOString(),
                  lastError: `Local cleanup pending: ${message}`.slice(0, 240),
                  updatedAt: new Date().toISOString(),
                }
              : item,
          ),
        );
        continue;
      }
      const attempts = entry.attempts + 1;
      const missing = message.includes('manually removed');
      const failedEntry = {
        ...entry,
        state: missing ? 'missing' : 'failed',
        attempts,
        nextRetryAt: missing
          ? null
          : new Date(
              Date.now() + Math.min(60 * 60_000, 15_000 * 2 ** Math.min(attempts, 8)),
            ).toISOString(),
        lastError: message.slice(0, 240),
        updatedAt: new Date().toISOString(),
      } as CaptureQueueEntry;
      await saveCaptureQueue(
        (await loadCaptureQueue()).map((item) =>
          item.captureId === entry!.captureId && item.fileName === entry!.fileName
            ? failedEntry
            : item,
        ),
      );
      reportLocationDebugEvent('glasses_capture_sync_error', {
        message: 'Capture reconciliation failed for one queue entry.',
        error: message,
        payload: { stage: 'capture_sync', capture_id: entry.captureId, attempts },
      });
      // Per-entry failures do not reject the overall reconciliation promise, so
      // surface the actionable backend/transfer error in the settings screen too.
      publish({ lastError: message.slice(0, 240) });
    }
  }
  const remaining = await loadCaptureQueue();
  const latestError =
    [...remaining].reverse().find((item) => item.state === 'failed' && item.lastError)?.lastError ??
    null;
  publish({
    pendingCount: remaining.filter((item) => !['uploaded', 'missing'].includes(item.state)).length,
    failedCount: remaining.filter((item) => item.state === 'failed').length,
    uploadedCount: remaining.filter((item) => item.state === 'uploaded').length,
    // A zero-item queue must not turn a failed camera-server connection into
    // a misleading "All captures are up to date" result. Preserve the actual
    // connection failure so the settings screen can guide a reconnect.
    lastError: latestError ? safeCaptureErrorMessage(latestError) : connectionError,
  });
}

export function reconcileGlassesCaptures(): Promise<void> {
  if (activeSync) return activeSync;
  debugCaptureStage('glasses_capture_sync_requested', 'Capture reconciliation requested.');
  publish({ running: true, lastError: null });
  activeSync = runSync()
    .then(() => {
      debugCaptureStage('glasses_capture_sync_finished', 'Capture reconciliation finished.');
      publish({ running: false, lastRunAt: new Date().toISOString() });
    })
    .catch((error) => {
      const message = safeCaptureErrorMessage(error);
      debugCaptureStage(
        'glasses_capture_sync_failed',
        'Capture reconciliation failed before completing the queue drain.',
        {
          error: message.slice(0, 240),
        },
      );
      reportLocationDebugEvent('glasses_capture_sync_failed', {
        message: 'Capture reconciliation failed before completing the queue drain.',
        error: message.slice(0, 240),
        payload: { stage: 'capture_sync' },
      });
      publish({
        running: false,
        lastRunAt: new Date().toISOString(),
        lastError: message,
        networkPath: 'unavailable',
      });
    })
    .finally(async () => {
      if (hotspotOpenedBySync) {
        hotspotOpenedBySync = false;
        await disableGlassesHotspot();
      } else {
        await releaseGlassesNetwork();
      }
      activeSync = null;
    });
  return activeSync;
}

export async function retryFailedGlassesCaptures(): Promise<void> {
  // Startup, resume, and the retry button can overlap. A running pass has
  // already captured its queue snapshot, so changing a failed entry under it
  // would otherwise be a no-op until some later external trigger.
  if (activeSync) await activeSync;
  const queue = await loadCaptureQueue();
  const retryCount = queue.filter((entry) => entry.state === 'failed').length;
  await saveCaptureQueue(
    queue.map((entry) =>
      entry.state === 'failed'
        ? { ...entry, state: 'discovered', nextRetryAt: null, lastError: null }
      : entry,
    ),
  );
  debugCaptureStage(
    'glasses_capture_retry_requested',
    'Reset failed local captures and starting a fresh reconciliation pass.',
    { retry_count: retryCount },
  );
  return reconcileGlassesCaptures();
}
