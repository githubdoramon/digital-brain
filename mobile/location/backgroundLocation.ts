import * as BackgroundTask from 'expo-background-task';
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';

import {
  getStoredGoogleIdTokenDiagnostics,
} from '@/auth/backgroundToken';
import {
  drainQueuedBackgroundLocations,
  enqueueBackgroundLocationEntry,
  getQueuedBackgroundLocationSummary,
} from '@/location/backgroundLocationQueue';
import { reportLocationDebugEvent } from '@/location/debugState';
import { API_BASE_URL } from '@/api/client';
import { getLocationRuntimeState } from '@/location/runtimeState';

const BACKGROUND_LOCATION_TASK = 'digitalbrain.background-location';
const BACKGROUND_LOCATION_DRAIN_TASK = 'digitalbrain.background-location-drain';
const BACKGROUND_DISTANCE_INTERVAL_METERS = 50;
const BACKGROUND_TIME_INTERVAL_MS = 5 * 60 * 1000;
const BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES = 15;

type BackgroundLocationSample = {
  coords?: {
    latitude?: number;
    longitude?: number;
    accuracy?: number | null;
  };
  timestamp?: number;
};

type BackgroundBatchContext = {
  batchId: string;
  sampleIndex: number;
  sampleCount: number;
  batchFirstCapturedAt: string | null;
  batchLastCapturedAt: string | null;
  executionContext: string;
};

type PostedBackgroundLocation = {
  lat: number;
  lon: number;
  capturedAtMs: number;
};

const BACKGROUND_POST_DEDUPE_MIN_DISTANCE_METERS = 15;
const BACKGROUND_POST_DEDUPE_MIN_SECONDS = 30;
const BACKGROUND_BUFFER_FLUSH_MIN_DISTANCE_METERS = 50;
const BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS = 60;

let lastPostedBackgroundLocation: PostedBackgroundLocation | null = null;
let lastAcceptedBufferedLocation: PostedBackgroundLocation | null = null;

function calculateDistanceMeters(
  first: Pick<PostedBackgroundLocation, 'lat' | 'lon'>,
  second: Pick<PostedBackgroundLocation, 'lat' | 'lon'>,
): number {
  const toRadians = (value: number) => (value * Math.PI) / 180;
  const earthRadiusMeters = 6371000;

  const latDelta = toRadians(second.lat - first.lat);
  const lonDelta = toRadians(second.lon - first.lon);
  const firstLatRadians = toRadians(first.lat);
  const secondLatRadians = toRadians(second.lat);

  const haversine =
    Math.sin(latDelta / 2) * Math.sin(latDelta / 2) +
    Math.cos(firstLatRadians) * Math.cos(secondLatRadians) * Math.sin(lonDelta / 2) * Math.sin(lonDelta / 2);
  const arc = 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
  return earthRadiusMeters * arc;
}

function buildDebugErrorPayload(error: unknown): {
  message: string;
  status?: number;
  authExpired?: boolean;
  contentType?: string;
  bodyPreview?: string;
  requestUrl?: string;
  tokenPresent?: boolean;
  authDiagnostics?: Record<string, unknown>;
} {
  const errorWithMeta = error as Error & {
    status?: number;
    authExpired?: boolean;
    contentType?: string;
    bodyPreview?: string;
    requestUrl?: string;
    tokenPresent?: boolean;
    authDiagnostics?: Record<string, unknown>;
  };
  return {
    message: errorWithMeta?.message || 'Unknown error',
    ...(typeof errorWithMeta?.status === 'number' ? { status: errorWithMeta.status } : {}),
    ...(typeof errorWithMeta?.authExpired === 'boolean'
      ? { authExpired: errorWithMeta.authExpired }
      : {}),
    ...(typeof errorWithMeta?.contentType === 'string' ? { contentType: errorWithMeta.contentType } : {}),
    ...(typeof errorWithMeta?.bodyPreview === 'string' ? { bodyPreview: errorWithMeta.bodyPreview } : {}),
    ...(typeof errorWithMeta?.requestUrl === 'string' ? { requestUrl: errorWithMeta.requestUrl } : {}),
    ...(typeof errorWithMeta?.tokenPresent === 'boolean' ? { tokenPresent: errorWithMeta.tokenPresent } : {}),
    ...(errorWithMeta?.authDiagnostics ? { authDiagnostics: errorWithMeta.authDiagnostics } : {}),
  };
}

async function queueBackgroundLocation(sample: BackgroundLocationSample, context: BackgroundBatchContext): Promise<void> {
  const latitude = Number(sample.coords?.latitude);
  const longitude = Number(sample.coords?.longitude);
  const capturedAtMs = Number(sample.timestamp || Date.now());
  const capturedAt = new Date(capturedAtMs).toISOString();
  const runtimeState = getLocationRuntimeState();
  const debugRequestId = `${context.batchId}:${context.sampleIndex}`;
  const sampleAgeSeconds = Math.max(0, Math.round((Date.now() - capturedAtMs) / 1000));
  const isBufferedFlush =
    runtimeState.appState === 'active' && sampleAgeSeconds >= BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    reportLocationDebugEvent('background_location_invalid', {
      message: 'Invalid background coordinates',
      payload: {
        debug_request_id: debugRequestId,
        batch_id: context.batchId,
        sample_index: context.sampleIndex,
        sample_count: context.sampleCount,
        batch_first_captured_at: context.batchFirstCapturedAt,
        batch_last_captured_at: context.batchLastCapturedAt,
        execution_context: context.executionContext,
        sample_age_seconds: sampleAgeSeconds,
        is_buffered_flush: isBufferedFlush,
        api_base_url: API_BASE_URL,
        app_state: runtimeState.appState,
      },
    });
    return;
  }

  if (lastPostedBackgroundLocation) {
    const movedMeters = calculateDistanceMeters(lastPostedBackgroundLocation, {
      lat: latitude,
      lon: longitude,
    });
    const elapsedSeconds = (capturedAtMs - lastPostedBackgroundLocation.capturedAtMs) / 1000;
    if (
      movedMeters < BACKGROUND_POST_DEDUPE_MIN_DISTANCE_METERS &&
      elapsedSeconds >= 0 &&
      elapsedSeconds < BACKGROUND_POST_DEDUPE_MIN_SECONDS
    ) {
      reportLocationDebugEvent('background_sync_skipped', {
        message: 'Skipped near-duplicate buffered background sample',
        recordInHistory: false,
        payload: {
          debug_request_id: debugRequestId,
          batch_id: context.batchId,
          sample_index: context.sampleIndex,
          sample_count: context.sampleCount,
          batch_first_captured_at: context.batchFirstCapturedAt,
          batch_last_captured_at: context.batchLastCapturedAt,
          moved_meters: Math.round(movedMeters),
          elapsed_seconds: Math.round(elapsedSeconds),
          api_base_url: API_BASE_URL,
          app_state: runtimeState.appState,
        },
      });
      return;
    }
  }

  if (isBufferedFlush && lastAcceptedBufferedLocation) {
    const movedMetersFromBuffered = calculateDistanceMeters(lastAcceptedBufferedLocation, {
      lat: latitude,
      lon: longitude,
    });
    if (capturedAtMs <= lastAcceptedBufferedLocation.capturedAtMs) {
      reportLocationDebugEvent('background_buffered_sync_skipped', {
        message: 'Skipped stale buffered sample during active flush',
        recordInHistory: false,
        payload: {
          debug_request_id: debugRequestId,
          batch_id: context.batchId,
          sample_index: context.sampleIndex,
          sample_count: context.sampleCount,
          batch_first_captured_at: context.batchFirstCapturedAt,
          batch_last_captured_at: context.batchLastCapturedAt,
          execution_context: context.executionContext,
          sample_age_seconds: sampleAgeSeconds,
          is_buffered_flush: true,
          last_accepted_captured_at: new Date(lastAcceptedBufferedLocation.capturedAtMs).toISOString(),
          api_base_url: API_BASE_URL,
          app_state: runtimeState.appState,
        },
      });
      return;
    }
    if (movedMetersFromBuffered < BACKGROUND_BUFFER_FLUSH_MIN_DISTANCE_METERS) {
      reportLocationDebugEvent('background_buffered_sync_skipped', {
        message: 'Skipped buffered sample below movement threshold during active flush',
        recordInHistory: false,
        payload: {
          debug_request_id: debugRequestId,
          batch_id: context.batchId,
          sample_index: context.sampleIndex,
          sample_count: context.sampleCount,
          batch_first_captured_at: context.batchFirstCapturedAt,
          batch_last_captured_at: context.batchLastCapturedAt,
          execution_context: context.executionContext,
          sample_age_seconds: sampleAgeSeconds,
          is_buffered_flush: true,
          moved_meters: Math.round(movedMetersFromBuffered),
          threshold_meters: BACKGROUND_BUFFER_FLUSH_MIN_DISTANCE_METERS,
          api_base_url: API_BASE_URL,
          app_state: runtimeState.appState,
        },
      });
      return;
    }
  }

  const tokenDiagnostics = await getStoredGoogleIdTokenDiagnostics();
  const accuracyRaw = Number(sample.coords?.accuracy);
  const accuracy = Number.isFinite(accuracyRaw) ? Math.round(accuracyRaw * 10) / 10 : undefined;

  lastPostedBackgroundLocation = {
    lat: latitude,
    lon: longitude,
    capturedAtMs,
  };
  if (isBufferedFlush) {
    lastAcceptedBufferedLocation = {
      lat: latitude,
      lon: longitude,
      capturedAtMs,
    };
  }

  await enqueueBackgroundLocationEntry({
    id: `${capturedAtMs}:${latitude.toFixed(6)}:${longitude.toFixed(6)}`,
    lat: latitude,
    lon: longitude,
    accuracyM: accuracy,
    capturedAt,
    capturedAtMs,
    source: 'expo_location',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || undefined,
    debugRequestId,
    batchId: context.batchId,
    sampleIndex: context.sampleIndex,
    sampleCount: context.sampleCount,
    batchFirstCapturedAt: context.batchFirstCapturedAt,
    batchLastCapturedAt: context.batchLastCapturedAt,
    executionContext: context.executionContext,
    sampleAgeSeconds,
    isBufferedFlush,
    enqueuedAt: new Date().toISOString(),
    attemptCount: 0,
  });

  reportLocationDebugEvent('background_queue_ready_for_drain', {
    payload: {
      debug_request_id: debugRequestId,
      batch_id: context.batchId,
      sample_index: context.sampleIndex,
      sample_count: context.sampleCount,
      batch_first_captured_at: context.batchFirstCapturedAt,
      batch_last_captured_at: context.batchLastCapturedAt,
      execution_context: context.executionContext,
      sample_age_seconds: sampleAgeSeconds,
      is_buffered_flush: isBufferedFlush,
      lat: latitude,
      lon: longitude,
      captured_at: capturedAt,
      api_base_url: API_BASE_URL,
      app_state: runtimeState.appState,
      ...tokenDiagnostics,
    },
    recordInHistory: false,
  });

  await drainQueuedBackgroundLocations('location_task');
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_DRAIN_TASK, async () => {
    try {
      await drainQueuedBackgroundLocations('background_task_worker');
      return BackgroundTask.BackgroundTaskResult.Success;
    } catch (error) {
      reportLocationDebugEvent('background_queue_drain_task_error', {
        error,
      });
      return BackgroundTask.BackgroundTaskResult.Failed;
    }
  });
}

async function ensureBackgroundDrainTaskRegistered(): Promise<void> {
  const backgroundTaskStatus = await BackgroundTask.getStatusAsync();
  const resolvedBackgroundTaskStatus =
    typeof backgroundTaskStatus === 'number'
      ? BackgroundTask.BackgroundTaskStatus[backgroundTaskStatus] ?? String(backgroundTaskStatus)
      : 'unknown';

  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_LOCATION_DRAIN_TASK);
  if (isRegistered) {
    reportLocationDebugEvent('background_drain_task_already_registered', {
      payload: {
        background_task_status: resolvedBackgroundTaskStatus,
      },
      recordInHistory: false,
    });
    return;
  }

  await BackgroundTask.registerTaskAsync(BACKGROUND_LOCATION_DRAIN_TASK, {
    minimumInterval: BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES,
  });
  reportLocationDebugEvent('background_drain_task_registered', {
    payload: {
      minimum_interval_minutes: BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES,
      background_task_status: resolvedBackgroundTaskStatus,
    },
    recordInHistory: false,
  });
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_TASK, async ({ data, error, executionInfo }) => {
    const locations = ((data as { locations?: BackgroundLocationSample[] } | undefined)?.locations ?? []).filter(
      Boolean,
    );
    const batchId = `${Date.now()}-${locations.length}`;
    const batchTimestamps = locations
      .map((location) => Number(location.timestamp))
      .filter((timestamp) => Number.isFinite(timestamp) && timestamp > 0)
      .sort((first, second) => first - second);
    const batchFirstCapturedAt = batchTimestamps.length ? new Date(batchTimestamps[0]).toISOString() : null;
    const batchLastCapturedAt = batchTimestamps.length
      ? new Date(batchTimestamps[batchTimestamps.length - 1]).toISOString()
      : null;
    const oldestSampleAgeSeconds = batchTimestamps.length
      ? Math.max(0, Math.round((Date.now() - batchTimestamps[0]) / 1000))
      : 0;
    const executionContext = executionInfo?.appState ?? getLocationRuntimeState().appState ?? 'unknown';

    if (error) {
      reportLocationDebugEvent('background_task_error', {
        error,
        payload: {
          batch_id: batchId,
          sample_count: locations.length,
          batch_first_captured_at: batchFirstCapturedAt,
          batch_last_captured_at: batchLastCapturedAt,
          execution_context: executionContext,
          oldest_sample_age_seconds: oldestSampleAgeSeconds,
        },
      });
      return;
    }

    reportLocationDebugEvent('background_task_batch_received', {
      payload: {
        batch_id: batchId,
        sample_count: locations.length,
        batch_first_captured_at: batchFirstCapturedAt,
        batch_last_captured_at: batchLastCapturedAt,
        execution_context: executionContext,
        oldest_sample_age_seconds: oldestSampleAgeSeconds,
        will_flush_buffered_samples:
          executionContext === 'active' && oldestSampleAgeSeconds >= BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS,
      },
    });

    if (executionContext === 'active' && oldestSampleAgeSeconds >= BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS) {
      reportLocationDebugEvent('background_buffered_flush_detected', {
        payload: {
          batch_id: batchId,
          sample_count: locations.length,
          batch_first_captured_at: batchFirstCapturedAt,
          batch_last_captured_at: batchLastCapturedAt,
          execution_context: executionContext,
          oldest_sample_age_seconds: oldestSampleAgeSeconds,
        },
        recordInHistory: false,
      });
    }

    if (!locations.length) {
      reportLocationDebugEvent('background_task_empty', {
        message: 'Background task had no location samples',
        payload: {
          batch_id: batchId,
          sample_count: locations.length,
          batch_first_captured_at: batchFirstCapturedAt,
          batch_last_captured_at: batchLastCapturedAt,
          execution_context: executionContext,
          oldest_sample_age_seconds: oldestSampleAgeSeconds,
        },
      });
      return;
    }

    for (const [index, location] of locations.entries()) {
      try {
        await queueBackgroundLocation(location, {
          batchId,
          sampleIndex: index + 1,
          sampleCount: locations.length,
          batchFirstCapturedAt,
          batchLastCapturedAt,
          executionContext,
        });
      } catch (taskError) {
        const errorPayload = buildDebugErrorPayload(taskError);
        reportLocationDebugEvent('background_sync_error', {
          message: errorPayload.message,
          error: taskError,
          payload: {
            batch_id: batchId,
            sample_index: index + 1,
            sample_count: locations.length,
            batch_first_captured_at: batchFirstCapturedAt,
            batch_last_captured_at: batchLastCapturedAt,
            execution_context: executionContext,
            oldest_sample_age_seconds: oldestSampleAgeSeconds,
            status: errorPayload.status,
            auth_expired: errorPayload.authExpired,
            content_type: errorPayload.contentType,
            response_preview: errorPayload.bodyPreview,
            request_url: errorPayload.requestUrl,
            token_present: errorPayload.tokenPresent,
            api_base_url: API_BASE_URL,
            app_state: getLocationRuntimeState().appState,
            ...(errorPayload.authDiagnostics ?? {}),
          },
        });
      }
    }
  });
}

export type BackgroundLocationDebugStatus = {
  foregroundPermission: string;
  backgroundPermission: string;
  taskStarted: boolean;
  drainTaskRegistered: boolean;
  backgroundTaskStatus: string;
  queuedLocationCount: number;
  oldestQueuedCapturedAt: string | null;
  newestQueuedCapturedAt: string | null;
};

export async function getBackgroundLocationDebugStatus(): Promise<BackgroundLocationDebugStatus> {
  const [foregroundPermission, backgroundPermission, taskStarted, drainTaskRegistered, backgroundTaskStatus, queueSummary] = await Promise.all([
    Location.getForegroundPermissionsAsync(),
    Location.getBackgroundPermissionsAsync(),
    Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK),
    TaskManager.isTaskRegisteredAsync(BACKGROUND_LOCATION_DRAIN_TASK),
    BackgroundTask.getStatusAsync(),
    getQueuedBackgroundLocationSummary(),
  ]);

  const resolvedBackgroundTaskStatus =
    typeof backgroundTaskStatus === 'number'
      ? BackgroundTask.BackgroundTaskStatus[backgroundTaskStatus] ?? String(backgroundTaskStatus)
      : 'unknown';

  reportLocationDebugEvent('background_worker_status_snapshot', {
    payload: {
      location_task_started: taskStarted,
      drain_task_registered: drainTaskRegistered,
      background_task_status: resolvedBackgroundTaskStatus,
      queued_location_count: queueSummary.queueSize,
      oldest_queued_captured_at: queueSummary.oldestCapturedAt,
      newest_queued_captured_at: queueSummary.newestCapturedAt,
    },
    recordInHistory: false,
  });

  return {
    foregroundPermission: foregroundPermission.status,
    backgroundPermission: backgroundPermission.status,
    taskStarted,
    drainTaskRegistered,
    backgroundTaskStatus: resolvedBackgroundTaskStatus,
    queuedLocationCount: queueSummary.queueSize,
    oldestQueuedCapturedAt: queueSummary.oldestCapturedAt,
    newestQueuedCapturedAt: queueSummary.newestCapturedAt,
  };
}

export async function syncBackgroundLocationTracking(enabled: boolean): Promise<void> {
  reportLocationDebugEvent('background_tracking_sync_requested', {
    payload: { enabled },
  });
  const alreadyStarted = await Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);

  if (!enabled) {
    if (alreadyStarted) {
      await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);
      reportLocationDebugEvent('background_tracking_stopped');
    }
    await BackgroundTask.unregisterTaskAsync(BACKGROUND_LOCATION_DRAIN_TASK).catch(() => undefined);
    return;
  }

  const foregroundPermission = await Location.getForegroundPermissionsAsync();
  const foregroundStatus =
    foregroundPermission.status === 'granted'
      ? foregroundPermission.status
      : (await Location.requestForegroundPermissionsAsync()).status;
  if (foregroundStatus !== 'granted') {
    reportLocationDebugEvent('background_tracking_blocked', {
      message: 'Foreground permission not granted',
      payload: { foreground_status: foregroundStatus },
    });
    return;
  }

  const backgroundPermission = await Location.getBackgroundPermissionsAsync();
  const backgroundStatus =
    backgroundPermission.status === 'granted'
      ? backgroundPermission.status
      : (await Location.requestBackgroundPermissionsAsync()).status;
  if (backgroundStatus !== 'granted') {
    reportLocationDebugEvent('background_tracking_blocked', {
      message: 'Background permission not granted',
      payload: { background_status: backgroundStatus },
    });
    return;
  }

  if (alreadyStarted) {
    await ensureBackgroundDrainTaskRegistered().catch((error) => {
      reportLocationDebugEvent('background_drain_task_register_error', {
        error,
      });
    });
    reportLocationDebugEvent('background_tracking_already_started');
    return;
  }

  await Location.startLocationUpdatesAsync(BACKGROUND_LOCATION_TASK, {
    accuracy: Location.Accuracy.Balanced,
    distanceInterval: BACKGROUND_DISTANCE_INTERVAL_METERS,
    timeInterval: BACKGROUND_TIME_INTERVAL_MS,
    deferredUpdatesDistance: BACKGROUND_DISTANCE_INTERVAL_METERS,
    deferredUpdatesInterval: BACKGROUND_TIME_INTERVAL_MS,
    pausesUpdatesAutomatically: true,
    showsBackgroundLocationIndicator: false,
    foregroundService: {
      notificationTitle: 'Digital Brain location updates',
      notificationBody: 'Location updates are used to keep your context accurate.',
    },
  });
  await ensureBackgroundDrainTaskRegistered().catch((error) => {
    reportLocationDebugEvent('background_drain_task_register_error', {
      error,
    });
  });
  reportLocationDebugEvent('background_tracking_started');
}
