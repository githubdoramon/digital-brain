import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';

import { apiFetch } from '@/api/client';
import { getStoredGoogleIdToken, refreshStoredGoogleIdToken } from '@/auth/backgroundToken';
import { reportLocationDebugEvent } from '@/location/debugState';

const BACKGROUND_LOCATION_TASK = 'digitalbrain.background-location';
const BACKGROUND_DISTANCE_INTERVAL_METERS = 50;
const BACKGROUND_TIME_INTERVAL_MS = 5 * 60 * 1000;

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
};

type PostedBackgroundLocation = {
  lat: number;
  lon: number;
  capturedAtMs: number;
};

const BACKGROUND_POST_DEDUPE_MIN_DISTANCE_METERS = 15;
const BACKGROUND_POST_DEDUPE_MIN_SECONDS = 30;

let lastPostedBackgroundLocation: PostedBackgroundLocation | null = null;

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

function buildDebugErrorPayload(error: unknown): { message: string; status?: number; authExpired?: boolean } {
  const errorWithMeta = error as Error & { status?: number; authExpired?: boolean };
  return {
    message: errorWithMeta?.message || 'Unknown error',
    ...(typeof errorWithMeta?.status === 'number' ? { status: errorWithMeta.status } : {}),
    ...(typeof errorWithMeta?.authExpired === 'boolean'
      ? { authExpired: errorWithMeta.authExpired }
      : {}),
  };
}

async function postBackgroundLocation(sample: BackgroundLocationSample, context: BackgroundBatchContext): Promise<void> {
  const latitude = Number(sample.coords?.latitude);
  const longitude = Number(sample.coords?.longitude);
  const capturedAtMs = Number(sample.timestamp || Date.now());
  const capturedAt = new Date(capturedAtMs).toISOString();
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    reportLocationDebugEvent('background_location_invalid', {
      message: 'Invalid background coordinates',
      payload: {
        batch_id: context.batchId,
        sample_index: context.sampleIndex,
        sample_count: context.sampleCount,
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
          batch_id: context.batchId,
          sample_index: context.sampleIndex,
          sample_count: context.sampleCount,
          moved_meters: Math.round(movedMeters),
          elapsed_seconds: Math.round(elapsedSeconds),
        },
      });
      return;
    }
  }

  const token = await getStoredGoogleIdToken();
  if (!token) {
    reportLocationDebugEvent('background_sync_skipped', {
      message: 'Missing auth token in secure store',
      payload: {
        batch_id: context.batchId,
        sample_index: context.sampleIndex,
        sample_count: context.sampleCount,
      },
    });
    return;
  }

  const accuracyRaw = Number(sample.coords?.accuracy);
  const accuracy = Number.isFinite(accuracyRaw) ? Math.round(accuracyRaw * 10) / 10 : undefined;

  reportLocationDebugEvent('background_sync_attempt', {
    payload: {
      batch_id: context.batchId,
      sample_index: context.sampleIndex,
      sample_count: context.sampleCount,
      lat: latitude,
      lon: longitude,
      captured_at: capturedAt,
    },
  });

  await apiFetch('/mobile/location', {
    method: 'POST',
    token,
    onAuthExpired: refreshStoredGoogleIdToken,
    body: JSON.stringify({
      lat: latitude,
      lon: longitude,
      accuracy_m: accuracy,
      captured_at: capturedAt,
      source: 'expo_location',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || undefined,
    }),
  });

  lastPostedBackgroundLocation = {
    lat: latitude,
    lon: longitude,
    capturedAtMs,
  };

  reportLocationDebugEvent('background_sync_success', {
    payload: {
      batch_id: context.batchId,
      sample_index: context.sampleIndex,
      sample_count: context.sampleCount,
      lat: latitude,
      lon: longitude,
    },
  });
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_TASK, async ({ data, error }) => {
    const locations = ((data as { locations?: BackgroundLocationSample[] } | undefined)?.locations ?? []).filter(
      Boolean,
    );
    const batchId = `${Date.now()}-${locations.length}`;

    if (error) {
      reportLocationDebugEvent('background_task_error', {
        error,
        payload: {
          batch_id: batchId,
          sample_count: locations.length,
        },
      });
      return;
    }

    reportLocationDebugEvent('background_task_batch_received', {
      payload: {
        batch_id: batchId,
        sample_count: locations.length,
      },
    });

    if (!locations.length) {
      reportLocationDebugEvent('background_task_empty', {
        message: 'Background task had no location samples',
        payload: {
          batch_id: batchId,
        },
      });
      return;
    }

    for (const [index, location] of locations.entries()) {
      try {
        await postBackgroundLocation(location, {
          batchId,
          sampleIndex: index + 1,
          sampleCount: locations.length,
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
            status: errorPayload.status,
            auth_expired: errorPayload.authExpired,
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
};

export async function getBackgroundLocationDebugStatus(): Promise<BackgroundLocationDebugStatus> {
  const [foregroundPermission, backgroundPermission, taskStarted] = await Promise.all([
    Location.getForegroundPermissionsAsync(),
    Location.getBackgroundPermissionsAsync(),
    Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK),
  ]);

  return {
    foregroundPermission: foregroundPermission.status,
    backgroundPermission: backgroundPermission.status,
    taskStarted,
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
  reportLocationDebugEvent('background_tracking_started');
}
