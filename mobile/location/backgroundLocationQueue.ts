import AsyncStorage from '@react-native-async-storage/async-storage';

import { apiFetch, API_BASE_URL } from '@/api/client';
import {
  getStoredGoogleIdToken,
  getStoredGoogleIdTokenDiagnostics,
  refreshStoredGoogleIdToken,
} from '@/auth/backgroundToken';
import { reportLocationDebugEvent } from '@/location/debugState';
import { getLocationRuntimeState } from '@/location/runtimeState';

const BACKGROUND_LOCATION_QUEUE_KEY = 'digitalbrain.backgroundLocationQueue';
const MAX_QUEUED_BACKGROUND_LOCATIONS = 200;

let drainInFlight: Promise<{
  initialQueueSize: number;
  drainedCount: number;
  remainingQueueSize: number;
}> | null = null;
let drainRerunRequested = false;

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

type DrainTrigger = 'location_task' | 'background_task_worker' | 'manual';

type ApiFetchError = Error & {
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

function describeSyncFailure(error: ApiFetchError): string {
  if (error.fetchFailed) {
    return 'request_failed_before_response';
  }
  if (error.authExpired) {
    return 'auth_expired';
  }
  if (typeof error.status === 'number') {
    if (error.status >= 500) {
      return 'backend_server_error';
    }
    if (error.status >= 400) {
      return 'backend_client_error';
    }
    return 'unexpected_http_status';
  }
  if (error.contentType && !error.contentType.includes('application/json')) {
    return 'unexpected_response_content_type';
  }
  return 'unknown_request_error';
}

async function readQueue(): Promise<QueuedBackgroundLocationEntry[]> {
  try {
    const raw = await AsyncStorage.getItem(BACKGROUND_LOCATION_QUEUE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as QueuedBackgroundLocationEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    reportLocationDebugEvent('background_queue_read_error', {
      error,
    });
    return [];
  }
}

async function writeQueue(queue: QueuedBackgroundLocationEntry[]): Promise<void> {
  await AsyncStorage.setItem(BACKGROUND_LOCATION_QUEUE_KEY, JSON.stringify(queue));
}

export async function enqueueBackgroundLocationEntry(entry: QueuedBackgroundLocationEntry): Promise<void> {
  const queue = await readQueue();
  if (queue.some((item) => item.id === entry.id)) {
    reportLocationDebugEvent('background_queue_deduped', {
      payload: {
        queue_size: queue.length,
        debug_request_id: entry.debugRequestId,
        batch_id: entry.batchId,
      },
      recordInHistory: false,
    });
    return;
  }

  const nextQueue = [entry, ...queue].sort((first, second) => first.capturedAtMs - second.capturedAtMs).slice(-MAX_QUEUED_BACKGROUND_LOCATIONS);
  await writeQueue(nextQueue);
  reportLocationDebugEvent('background_queue_enqueued', {
    payload: {
      queue_size: nextQueue.length,
      debug_request_id: entry.debugRequestId,
      batch_id: entry.batchId,
      captured_at: entry.capturedAt,
      is_buffered_flush: entry.isBufferedFlush,
    },
    recordInHistory: false,
  });
}

async function drainQueuedBackgroundLocationsInner(trigger: DrainTrigger): Promise<{
  initialQueueSize: number;
  drainedCount: number;
  remainingQueueSize: number;
}> {
  let queue = await readQueue();
  const initialQueueSize = queue.length;
  const runtimeState = getLocationRuntimeState();

  reportLocationDebugEvent('background_queue_drain_started', {
    payload: {
      trigger,
      queue_size: initialQueueSize,
      app_state: runtimeState.appState,
      will_attempt_request: initialQueueSize > 0,
    },
    recordInHistory: false,
  });

  if (!queue.length) {
    return { initialQueueSize, drainedCount: 0, remainingQueueSize: 0 };
  }

  let token = await getStoredGoogleIdToken();
  let tokenDiagnostics = await getStoredGoogleIdTokenDiagnostics();

  if (!token) {
    reportLocationDebugEvent('background_queue_drain_blocked', {
      message: 'Missing auth token while draining queued background locations',
      payload: {
        reason: 'missing_auth_token',
        request_attempted: false,
        trigger,
        queue_size: queue.length,
        app_state: runtimeState.appState,
        ...tokenDiagnostics,
      },
    });
    return { initialQueueSize, drainedCount: 0, remainingQueueSize: queue.length };
  }

  let drainedCount = 0;
  for (const entry of [...queue].sort((first, second) => first.capturedAtMs - second.capturedAtMs)) {
    const currentRuntimeState = getLocationRuntimeState();
    reportLocationDebugEvent('background_sync_attempt', {
      payload: {
        request_attempted: true,
        debug_request_id: entry.debugRequestId,
        batch_id: entry.batchId,
        sample_index: entry.sampleIndex,
        sample_count: entry.sampleCount,
        batch_first_captured_at: entry.batchFirstCapturedAt,
        batch_last_captured_at: entry.batchLastCapturedAt,
        execution_context: entry.executionContext,
        sample_age_seconds: entry.sampleAgeSeconds,
        is_buffered_flush: entry.isBufferedFlush,
        lat: entry.lat,
        lon: entry.lon,
        captured_at: entry.capturedAt,
        queue_trigger: trigger,
        queue_size: queue.length,
        attempt_count: entry.attemptCount,
        api_base_url: API_BASE_URL,
        request_url: `${API_BASE_URL}/mobile/location`,
        request_method: 'POST',
        app_state: currentRuntimeState.appState,
        last_app_state_change_at: currentRuntimeState.lastAppStateChangeAt,
        ...tokenDiagnostics,
      },
      recordInHistory: false,
    });

    try {
      await apiFetch('/mobile/location', {
        method: 'POST',
        token,
        onAuthExpired: async () => {
          const refreshedToken = await refreshStoredGoogleIdToken();
          token = refreshedToken;
          tokenDiagnostics = await getStoredGoogleIdTokenDiagnostics();
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

      queue = queue.filter((item) => item.id !== entry.id);
      await writeQueue(queue);
      drainedCount += 1;
      reportLocationDebugEvent('background_sync_success', {
        payload: {
          request_attempted: true,
          request_completed: true,
          debug_request_id: entry.debugRequestId,
          batch_id: entry.batchId,
          sample_index: entry.sampleIndex,
          sample_count: entry.sampleCount,
          batch_first_captured_at: entry.batchFirstCapturedAt,
          batch_last_captured_at: entry.batchLastCapturedAt,
          execution_context: entry.executionContext,
          sample_age_seconds: entry.sampleAgeSeconds,
          is_buffered_flush: entry.isBufferedFlush,
          lat: entry.lat,
          lon: entry.lon,
          captured_at: entry.capturedAt,
          queue_trigger: trigger,
          queue_size: queue.length,
          api_base_url: API_BASE_URL,
          request_url: `${API_BASE_URL}/mobile/location`,
          request_method: 'POST',
          app_state: getLocationRuntimeState().appState,
          ...tokenDiagnostics,
        },
      });
    } catch (error) {
      const fetchError = error as ApiFetchError;
      queue = queue.map((item) =>
        item.id === entry.id
          ? {
              ...item,
              attemptCount: item.attemptCount + 1,
              lastAttemptAt: new Date().toISOString(),
            }
          : item,
      );
      await writeQueue(queue);
      reportLocationDebugEvent('background_sync_error', {
        message: fetchError.message,
        error,
        payload: {
          reason: describeSyncFailure(fetchError),
          request_attempted: true,
          request_completed: !fetchError.fetchFailed,
          debug_request_id: entry.debugRequestId,
          batch_id: entry.batchId,
          sample_index: entry.sampleIndex,
          sample_count: entry.sampleCount,
          batch_first_captured_at: entry.batchFirstCapturedAt,
          batch_last_captured_at: entry.batchLastCapturedAt,
          execution_context: entry.executionContext,
          sample_age_seconds: entry.sampleAgeSeconds,
          is_buffered_flush: entry.isBufferedFlush,
          queue_trigger: trigger,
          queue_size: queue.length,
          attempt_count: entry.attemptCount + 1,
          status: fetchError.status,
          auth_expired: fetchError.authExpired,
          fetch_failed: fetchError.fetchFailed,
          content_type: fetchError.contentType,
          response_preview: fetchError.bodyPreview,
          request_url: fetchError.requestUrl,
          request_method: fetchError.requestMethod,
          token_present: fetchError.tokenPresent,
          api_base_url: API_BASE_URL,
          app_state: getLocationRuntimeState().appState,
          ...(fetchError.authDiagnostics ?? tokenDiagnostics),
        },
      });
      break;
    }
  }

  reportLocationDebugEvent('background_queue_drain_finished', {
    payload: {
      trigger,
      initial_queue_size: initialQueueSize,
      drained_count: drainedCount,
      remaining_queue_size: queue.length,
      app_state: getLocationRuntimeState().appState,
    },
    recordInHistory: false,
  });

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
    reportLocationDebugEvent('background_queue_drain_coalesced', {
      payload: {
        trigger,
      },
      recordInHistory: false,
    });
    return drainInFlight;
  }

  drainInFlight = (async () => {
    let lastResult = await drainQueuedBackgroundLocationsInner(trigger);
    while (drainRerunRequested) {
      drainRerunRequested = false;
      lastResult = await drainQueuedBackgroundLocationsInner('location_task');
    }
    return lastResult;
  })();

  try {
    return await drainInFlight;
  } finally {
    drainInFlight = null;
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
