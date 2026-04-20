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

async function postBackgroundLocation(sample: BackgroundLocationSample): Promise<void> {
  const latitude = Number(sample.coords?.latitude);
  const longitude = Number(sample.coords?.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    reportLocationDebugEvent('background_location_invalid', {
      message: 'Invalid background coordinates',
    });
    return;
  }

  const token = await getStoredGoogleIdToken();
  if (!token) {
    reportLocationDebugEvent('background_sync_skipped', {
      message: 'Missing auth token in secure store',
    });
    return;
  }

  const accuracyRaw = Number(sample.coords?.accuracy);
  const accuracy = Number.isFinite(accuracyRaw) ? Math.round(accuracyRaw * 10) / 10 : undefined;

  reportLocationDebugEvent('background_sync_attempt', {
    payload: {
      lat: latitude,
      lon: longitude,
      captured_at: new Date(sample.timestamp || Date.now()).toISOString(),
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
      captured_at: new Date(sample.timestamp || Date.now()).toISOString(),
      source: 'expo_location',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || undefined,
    }),
  });

  reportLocationDebugEvent('background_sync_success', {
    payload: {
      lat: latitude,
      lon: longitude,
    },
  });
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_TASK, async ({ data, error }) => {
    if (error) {
      reportLocationDebugEvent('background_task_error', {
        error,
      });
      return;
    }

    const locations = ((data as { locations?: BackgroundLocationSample[] } | undefined)?.locations ?? []).filter(
      Boolean,
    );
    const latest = locations[locations.length - 1];
    if (!latest) {
      reportLocationDebugEvent('background_task_empty', {
        message: 'Background task had no location samples',
      });
      return;
    }

    try {
      await postBackgroundLocation(latest);
    } catch (taskError) {
      reportLocationDebugEvent('background_sync_error', {
        error: taskError,
      });
      // Best effort only.
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
