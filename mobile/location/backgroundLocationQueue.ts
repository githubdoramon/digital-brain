import AsyncStorage from '@react-native-async-storage/async-storage';

import { apiFetch } from '@/api/client';
import { getStoredGoogleIdToken, refreshStoredGoogleIdToken } from '@/auth/backgroundToken';

import { getLocationRuntimeState } from '@/location/runtimeState';

const BACKGROUND_LOCATION_QUEUE_KEY = 'digitalbrain.backgroundLocationQueue';
const MAX_QUEUED_BACKGROUND_LOCATIONS = 200;
const MAX_BACKGROUND_LOCATION_UPLOADS_PER_DRAIN = 50;
const BACKGROUND_LOCATION_UPLOAD_TIMEOUT_MS = 15_000;
export const CAPTURE_LOCATION_TOLERANCE_MS = 10 * 60 * 1000;

let drainInFlight: Promise<{
  initialQueueSize: number;
  drainedCount: number;
  remainingQueueSize: number;
}> | null = null;
let drainRerunRequested = false;
let queueMutationInFlight: Promise<void> = Promise.resolve();

export type QueuedBackgroundLocationEntry = {
  id: string;
  lat: number;
  lon: number;
  accuracyM?: number;
  capturedAt: string;
  capturedAtMs: number;
  source: string;
  timezone?: string;
  debugRequestId: string;
  batchId: string;
  sampleIndex: number;
  sampleCount: number;
  batchFirstCapturedAt: string | null;
  batchLastCapturedAt: string | null;
  executionContext: string;
  sampleAgeSeconds: number;
  isBufferedFlush: boolean;
  enqueuedAt: string;
  attemptCount: number;
  lastAttemptAt?: string;
};

export type DrainTrigger = 'background_task_worker' | 'manual' | 'foreground_service';

export type HistoricalLocationSample = {
  lat: number;
  lon: number;
  accuracy_m?: number;
  captured_at: string;
  source: string;
};

let pendingDrainTrigger: DrainTrigger | null = null;

type _ApiFetchError = Error & {
  status?: number;
  authExpired?: boolean;
  contentType?: string;
  bodyPreview?: string;
  requestUrl?: string;
  tokenPresent?: boolean;
  authDiagnostics?: Record<string, unknown>;
  requestMethod?: string;
  fetchFailed?: boolean;
};

async function readQueue(): Promise<QueuedBackgroundLocationEntry[]> {
  try {
    const raw = await AsyncStorage.getItem(BACKGROUND_LOCATION_QUEUE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as QueuedBackgroundLocationEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/**
 * Find the closest locally queued location sample without requiring auth or a
 * network request. The background drain may not have uploaded this sample yet.
 */
export async function findNearestQueuedLocation(
  capturedAt: string,
  toleranceMs = CAPTURE_LOCATION_TOLERANCE_MS,
): Promise<(HistoricalLocationSample & { offsetMs: number }) | null> {
  const targetMs = Date.parse(capturedAt);
  if (!Number.isFinite(targetMs)) return null;
  const queue = await readQueue();
  let closest: (HistoricalLocationSample & { offsetMs: number }) | null = null;
  for (const entry of queue) {
    const sampleMs = Number(entry.capturedAtMs) || Date.parse(entry.capturedAt);
    const offsetMs = Math.abs(sampleMs - targetMs);
    if (!Number.isFinite(sampleMs) || offsetMs > toleranceMs) continue;
    const lat = Number(entry.lat);
    const lon = Number(entry.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    if (!closest || offsetMs < closest.offsetMs) {
      closest = {
        lat,
        lon,
        ...(Number.isFinite(Number(entry.accuracyM))
          ? { accuracy_m: Number(entry.accuracyM) }
          : {}),
        captured_at: entry.capturedAt,
        source: entry.source || 'unknown',
        offsetMs,
      };
    }
  }
  return closest;
}

async function writeQueue(queue: QueuedBackgroundLocationEntry[]): Promise<void> {
  await AsyncStorage.setItem(BACKGROUND_LOCATION_QUEUE_KEY, JSON.stringify(queue));
}

async function mutateQueue<T>(
  mutator:
    | ((queue: QueuedBackgroundLocationEntry[]) => Promise<{
        queue: QueuedBackgroundLocationEntry[];
        result: T;
      }>)
    | ((queue: QueuedBackgroundLocationEntry[]) => {
        queue: QueuedBackgroundLocationEntry[];
        result: T;
      }),
): Promise<T> {
  let result!: T;

  queueMutationInFlight = queueMutationInFlight
    .catch(() => undefined)
    .then(async () => {
      const queue = await readQueue();
      const mutation = await mutator(queue);
      await writeQueue(mutation.queue);
      result = mutation.result;
    });

  await queueMutationInFlight;
  return result;
}

export async function enqueueBackgroundLocationEntry(
  entry: QueuedBackgroundLocationEntry,
): Promise<void> {
  const outcome = await mutateQueue((queue) => {
    if (queue.some((item) => item.id === entry.id)) {
      return {
        queue,
        result: {
          deduped: true,
          queueSize: queue.length,
        },
      };
    }

    const nextQueue = [entry, ...queue]
      .sort((first, second) => first.capturedAtMs - second.capturedAtMs)
      .slice(-MAX_QUEUED_BACKGROUND_LOCATIONS);

    return {
      queue: nextQueue,
      result: {
        deduped: false,
        queueSize: nextQueue.length,
      },
    };
  });

  if (outcome.deduped) {
    return;
  }
}

async function drainQueuedBackgroundLocationsInner(
  trigger: DrainTrigger,
  deadline: number,
): Promise<{
  initialQueueSize: number;
  drainedCount: number;
  remainingQueueSize: number;
}> {
  let queue = await readQueue();
  const initialQueueSize = queue.length;

  if (!queue.length) {
    return { initialQueueSize, drainedCount: 0, remainingQueueSize: 0 };
  }

  let token = await getStoredGoogleIdToken();

  if (!token) {
    return { initialQueueSize, drainedCount: 0, remainingQueueSize: queue.length };
  }

  let drainedCount = 0;
  const drainBatch = [...queue]
    .sort((first, second) => first.capturedAtMs - second.capturedAtMs)
    .slice(0, MAX_BACKGROUND_LOCATION_UPLOADS_PER_DRAIN);
  for (const entry of drainBatch) {
    // Leave enough time for one bounded request; native runtime work never waits
    // through an unbounded backlog and resumes on the next service opportunity.
    if (Date.now() + BACKGROUND_LOCATION_UPLOAD_TIMEOUT_MS > deadline) break;
    const currentRuntimeState = getLocationRuntimeState();

    const abortController = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const uploadTimeout = abortController
      ? setTimeout(() => abortController.abort(), BACKGROUND_LOCATION_UPLOAD_TIMEOUT_MS)
      : null;
    try {
      await apiFetch('/mobile/location', {
        method: 'POST',
        ...(abortController ? { signal: abortController.signal } : {}),
        token,
        onAuthExpired: async () => {
          const refreshedToken = await refreshStoredGoogleIdToken();
          token = refreshedToken;
          return refreshedToken;
        },
        headers: {
          'x-location-debug-request-id': entry.debugRequestId,
          'x-location-debug-batch-id': entry.batchId,
          'x-location-debug-sample-index': String(entry.sampleIndex),
          'x-location-debug-sample-count': String(entry.sampleCount),
          'x-location-debug-captured-at': entry.capturedAt,
          'x-location-debug-app-state': String(currentRuntimeState.appState),
        },
        body: JSON.stringify({
          lat: entry.lat,
          lon: entry.lon,
          accuracy_m: entry.accuracyM,
          captured_at: entry.capturedAt,
          source: entry.source,
          timezone: entry.timezone,
        }),
      });
      if (uploadTimeout) {
        clearTimeout(uploadTimeout);
      }

      queue = await mutateQueue((currentQueue) => {
        const nextQueue = currentQueue.filter((item) => item.id !== entry.id);
        return {
          queue: nextQueue,
          result: nextQueue,
        };
      });
      drainedCount += 1;
    } catch {
      if (uploadTimeout) {
        clearTimeout(uploadTimeout);
      }

      queue = await mutateQueue((currentQueue) => {
        const lastAttemptAt = new Date().toISOString();
        const nextQueue = currentQueue.map((item) =>
          item.id === entry.id
            ? {
                ...item,
                attemptCount: item.attemptCount + 1,
                lastAttemptAt,
              }
            : item,
        );
        return {
          queue: nextQueue,
          result: nextQueue,
        };
      });

      break;
    }
  }

  return {
    initialQueueSize,
    drainedCount,
    remainingQueueSize: queue.length,
  };
}

export async function drainQueuedBackgroundLocations(trigger: DrainTrigger): Promise<{
  initialQueueSize: number;
  drainedCount: number;
  remainingQueueSize: number;
}> {
  if (drainInFlight) {
    drainRerunRequested = true;
    pendingDrainTrigger = trigger;

    return drainInFlight;
  }

  const deadline = Date.now() + 45_000;
  drainInFlight = (async () => {
    let lastResult = await drainQueuedBackgroundLocationsInner(trigger, deadline);
    while (drainRerunRequested && Date.now() + BACKGROUND_LOCATION_UPLOAD_TIMEOUT_MS <= deadline) {
      drainRerunRequested = false;
      const rerunTrigger = pendingDrainTrigger ?? trigger;
      pendingDrainTrigger = null;
      lastResult = await drainQueuedBackgroundLocationsInner(rerunTrigger, deadline);
    }
    return lastResult;
  })();

  try {
    return await drainInFlight;
  } finally {
    drainInFlight = null;
    drainRerunRequested = false;
    pendingDrainTrigger = null;
  }
}

export async function getQueuedBackgroundLocationSummary(): Promise<{
  queueSize: number;
  oldestCapturedAt: string | null;
  newestCapturedAt: string | null;
}> {
  const queue = await readQueue();
  const sorted = [...queue].sort((first, second) => first.capturedAtMs - second.capturedAtMs);
  return {
    queueSize: queue.length,
    oldestCapturedAt: sorted[0]?.capturedAt ?? null,
    newestCapturedAt: sorted[sorted.length - 1]?.capturedAt ?? null,
  };
}
