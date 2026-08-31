import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import { apiFetch, getAuthRequestContext } from '@/api/client';
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
  deleteLocalCapture,
  ensurePrivateCaptureDirectory,
  getCaptureFolderUri,
  getLocalCaptureInfo,
  loadCaptureQueue,
  normalizeSafDirectoryUri,
  privateCapturePath,
  saveCaptureQueue,
} from './storage';
import type { CaptureQueueEntry, CaptureSyncStatus, RemoteCapture } from './types';
import { resolveCaptureLocation } from './location';

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
  const storedFolder = await getCaptureFolderUri();
  const folder = storedFolder ? normalizeSafDirectoryUri(storedFolder) : null;
  if (folder) {
    let visibleTarget: string | null = null;
    try {
      const visibleName = `${remote.captureId.replace(/[^a-zA-Z0-9._-]/g, '_')}-${mediaLeafName(remote.fileName)}`;
      const existing = (
        await FileSystem.StorageAccessFramework.readDirectoryAsync(folder).catch(() => [])
      ).find((uri) => decodeURIComponent(uri).endsWith(`/${visibleName}`));
      if (existing) {
        const existingInfo = await getLocalCaptureInfo(existing);
        if (existingInfo.exists && existingInfo.size === downloaded.size) {
          await deleteLocalCapture(temporary);
          return existing;
        }
        await deleteLocalCapture(existing);
      }
      visibleTarget = await FileSystem.StorageAccessFramework.createFileAsync(
        folder,
        // v3 folder-based captures use names such as IMG_xxx/base.jpg. SAF creates a
        // single child and rejects a slash, so retain the extension while making the
        // visible name deterministic and collision-resistant.
        visibleName,
        remote.mimeType,
      );
      // copyAsync keeps the transfer native and bounded for videos. It also avoids a partially
      // transferred JS string if the media is larger than the app's heap budget.
      await FileSystem.copyAsync({ from: temporary, to: visibleTarget });
      const visible = await getLocalCaptureInfo(visibleTarget);
      if (!visible.exists || visible.size !== downloaded.size) {
        throw new Error('Visible capture copy failed validation');
      }
      await deleteLocalCapture(temporary);
      return visibleTarget;
    } catch {
      // A persisted SAF grant can expire or Android can reject a nested tree URI
      // even though the folder was previously selected. Never let the optional
      // Files-app copy block acknowledgement and Immich upload: retain the media
      // in the app-private queue and continue the durable sync flow.
      if (visibleTarget) await deleteLocalCapture(visibleTarget).catch(() => undefined);
      debugCaptureStage(
        'glasses_capture_visible_copy_unavailable',
        'Documents-folder copy unavailable; using app-private queue.',
      );
    }
  }
  const target = privateCapturePath(remote.fileName);
  await FileSystem.deleteAsync(target, { idempotent: true });
  await FileSystem.moveAsync({ from: temporary, to: target });
  return target;
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
  const form = new FormData();
  form.append('capture_id', entry.captureId);
  if (entry.capturedAt) form.append('captured_at', entry.capturedAt);
  if (location) form.append('location', JSON.stringify(location));
  form.append('file', {
    uri: entry.localUri,
    name: mediaLeafName(entry.fileName),
    type: entry.mimeType,
  } as unknown as Blob);
  const response = (await apiFetch('/mobile/glasses/captures', {
    method: 'POST',
    body: form,
    token,
    onAuthExpired: async () => {
      token = await refreshStoredGoogleIdToken();
      return token;
    },
  })) as any;
  return String(response?.capture?.immich_asset_id ?? '');
}

async function runSync(): Promise<void> {
  debugCaptureStage('glasses_capture_sync_starting', 'Starting glasses capture reconciliation.');
  if (Platform.OS !== 'android')
    throw new Error('Glasses capture sync is Android-only in this release');
  // A capture signal can arrive while the glasses camera is still busy. Reconnect/readiness is
  // safe here, but replaying gallery/photo/video settings during that camera transaction can
  // race the firmware and make a physical-button capture appear to do nothing. Defaults are
  // applied on pairing, app startup, and the explicit settings action instead.
  let connection: Awaited<ReturnType<typeof connectGalleryServer>> | null = null;
  let connectionError: string | null = null;
  try {
    const connected = await ensureMentraConnection({ applyCaptureDefaults: false });
    if (!connected) {
      throw new Error(
        'No Mentra Live is paired. Open Settings → Glasses capture and pair the glasses.',
      );
    }
    connection = await connectGalleryServer();
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
  const queue = await loadCaptureQueue();
  await saveCaptureQueue(
    queue.map((entry) =>
      entry.state === 'failed'
        ? { ...entry, state: 'discovered', nextRetryAt: null, lastError: null }
        : entry,
    ),
  );
  return reconcileGlassesCaptures();
}
