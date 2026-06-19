import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import {
  enqueueBackgroundLocationEntry,
  getQueuedBackgroundLocationSummary,
} from '@/location/backgroundLocationQueue';
import {
  ensureBackgroundLocationDrainTaskRegistered,
  getBackgroundLocationDrainWorkerStatus,
  unregisterBackgroundLocationDrainTask,
} from '@/location/backgroundLocationDrainTask';
import {
  BACKGROUND_LOCATION_DRAIN_TASK,
  BACKGROUND_LOCATION_GEOFENCE_TASK,
  BACKGROUND_LOCATION_TASK,
} from '@/location/backgroundLocationTaskNames';
import { reportLocationDebugEvent } from '@/location/debugState';
import { API_BASE_URL } from '@/api/client';
import { getLocationRuntimeState } from '@/location/runtimeState';

const BACKGROUND_TRACKING_STATE_KEY = 'digitalbrain.backgroundLocationTrackingState';
const BACKGROUND_DISTANCE_INTERVAL_METERS = 5;
const BACKGROUND_TIME_INTERVAL_MS = 5 * 60 * 1000;
const ANDROID_LOCATION_MODE = 'hybrid_quiet_until_moving';
const IOS_LOCATION_MODE = 'continuous_background_updates';
const ANDROID_GEOFENCE_RADIUS_METERS = 30;
const ANDROID_RELIABLE_START_DISTANCE_METERS = 30;
const ANDROID_STATIONARY_RADIUS_METERS = 30;
const ANDROID_STATIONARY_MIN_MS = 5 * 60 * 1000;

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

type BackgroundTaskRegistrationSnapshot = {
  locationTaskRegistered: boolean;
  drainTaskRegistered: boolean;
  geofenceTaskRegistered: boolean;
  locationTaskDefined: boolean;
  drainTaskDefined: boolean;
  geofenceTaskDefined: boolean;
};

type BackgroundExecutionDiagnostics = {
  platform: string;
  locationServicesEnabled?: boolean;
  providerStatus?: Record<string, unknown>;
  foregroundPermissionDetails?: Record<string, unknown>;
  backgroundPermissionDetails?: Record<string, unknown>;
};

type AndroidTaskDiagnostics = {
  taskManagerAvailable: boolean | null;
  backgroundLocationAvailable: boolean | null;
  registeredTasks: Record<string, unknown>[];
  locationTaskOptions: Record<string, unknown> | null;
};

type AndroidCaptureMode = 'quiet' | 'reliable';

type AndroidTrackingState = {
  mode: AndroidCaptureMode;
  anchorLat?: number;
  anchorLon?: number;
  anchorCapturedAt?: string;
  stationarySinceMs?: number;
  updatedAt: string;
  reason?: string;
};

function getLocationMode(mode?: AndroidCaptureMode): string {
  if (Platform.OS !== 'android') {
    return IOS_LOCATION_MODE;
  }
  return `${ANDROID_LOCATION_MODE}:${mode ?? 'quiet'}`;
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([firstKey], [secondKey]) => firstKey.localeCompare(secondKey))
      .map(([key, entryValue]) => `${JSON.stringify(key)}:${stableStringify(entryValue)}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'undefined';
}

function areLocationTaskOptionsEqual(
  currentOptions: Record<string, unknown> | null,
  desiredOptions: Location.LocationTaskOptions,
): boolean {
  return (
    stableStringify(currentOptions ?? {}) ===
    stableStringify(desiredOptions as Record<string, unknown>)
  );
}

const BACKGROUND_POST_DEDUPE_MIN_DISTANCE_METERS = 15;
const BACKGROUND_POST_DEDUPE_MIN_SECONDS = 30;
const BACKGROUND_BUFFER_FLUSH_MIN_DISTANCE_METERS = 50;
const BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS = 60;

function getDefaultAndroidTrackingState(): AndroidTrackingState {
  return {
    mode: 'quiet',
    updatedAt: new Date().toISOString(),
    reason: 'default',
  };
}

async function getAndroidTrackingState(): Promise<AndroidTrackingState> {
  if (Platform.OS !== 'android') {
    return getDefaultAndroidTrackingState();
  }
  try {
    const raw = await AsyncStorage.getItem(BACKGROUND_TRACKING_STATE_KEY);
    if (!raw) {
      return getDefaultAndroidTrackingState();
    }
    const parsed = JSON.parse(raw) as Partial<AndroidTrackingState>;
    return {
      ...getDefaultAndroidTrackingState(),
      ...parsed,
      mode: parsed.mode === 'reliable' ? 'reliable' : 'quiet',
    };
  } catch (error) {
    reportLocationDebugEvent('background_tracking_state_read_error', {
      error,
    });
    return getDefaultAndroidTrackingState();
  }
}

async function setAndroidTrackingState(nextState: AndroidTrackingState): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }
  await AsyncStorage.setItem(BACKGROUND_TRACKING_STATE_KEY, JSON.stringify(nextState));
}

function buildBackgroundLocationTaskOptions(
  mode: AndroidCaptureMode = 'quiet',
): Location.LocationTaskOptions {
  const sharedOptions: Location.LocationTaskOptions = {
    accuracy: Location.Accuracy.Balanced,
    distanceInterval: BACKGROUND_DISTANCE_INTERVAL_METERS,
    timeInterval: BACKGROUND_TIME_INTERVAL_MS,
    showsBackgroundLocationIndicator: false,
  };

  if (Platform.OS === 'android') {
    const androidOptions: Location.LocationTaskOptions = {
      ...sharedOptions,
      pausesUpdatesAutomatically: false,
    };
    if (mode === 'reliable') {
      return {
        ...androidOptions,
        foregroundService: {
          notificationTitle: 'Digital Brain is tracing movement',
          notificationBody: 'Location updates are active until you stop moving.',
        },
      };
    }
    return androidOptions;
  }

  return {
    ...sharedOptions,
    deferredUpdatesDistance: BACKGROUND_DISTANCE_INTERVAL_METERS,
    deferredUpdatesInterval: BACKGROUND_TIME_INTERVAL_MS,
    pausesUpdatesAutomatically: true,
  };
}

let lastPostedBackgroundLocation: PostedBackgroundLocation | null = null;
let lastAcceptedBufferedLocation: PostedBackgroundLocation | null = null;
let trackingModeTransitionInFlight: Promise<void> | null = null;

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
    Math.cos(firstLatRadians) *
      Math.cos(secondLatRadians) *
      Math.sin(lonDelta / 2) *
      Math.sin(lonDelta / 2);
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
  requestMethod?: string;
  tokenPresent?: boolean;
  authDiagnostics?: Record<string, unknown>;
  fetchFailed?: boolean;
} {
  const errorWithMeta = error as Error & {
    status?: number;
    authExpired?: boolean;
    contentType?: string;
    bodyPreview?: string;
    requestUrl?: string;
    requestMethod?: string;
    tokenPresent?: boolean;
    authDiagnostics?: Record<string, unknown>;
    fetchFailed?: boolean;
  };
  return {
    message: errorWithMeta?.message || 'Unknown error',
    ...(typeof errorWithMeta?.status === 'number' ? { status: errorWithMeta.status } : {}),
    ...(typeof errorWithMeta?.authExpired === 'boolean'
      ? { authExpired: errorWithMeta.authExpired }
      : {}),
    ...(typeof errorWithMeta?.contentType === 'string'
      ? { contentType: errorWithMeta.contentType }
      : {}),
    ...(typeof errorWithMeta?.bodyPreview === 'string'
      ? { bodyPreview: errorWithMeta.bodyPreview }
      : {}),
    ...(typeof errorWithMeta?.requestUrl === 'string'
      ? { requestUrl: errorWithMeta.requestUrl }
      : {}),
    ...(typeof errorWithMeta?.requestMethod === 'string'
      ? { requestMethod: errorWithMeta.requestMethod }
      : {}),
    ...(typeof errorWithMeta?.tokenPresent === 'boolean'
      ? { tokenPresent: errorWithMeta.tokenPresent }
      : {}),
    ...(errorWithMeta?.authDiagnostics ? { authDiagnostics: errorWithMeta.authDiagnostics } : {}),
    ...(typeof errorWithMeta?.fetchFailed === 'boolean'
      ? { fetchFailed: errorWithMeta.fetchFailed }
      : {}),
  };
}

async function getTaskRegistrationSnapshot(): Promise<BackgroundTaskRegistrationSnapshot> {
  const [locationTaskRegistered, drainTaskRegistered] = await Promise.all([
    Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK),
    TaskManager.isTaskRegisteredAsync(BACKGROUND_LOCATION_DRAIN_TASK),
  ]);
  const geofenceTaskRegistered = await Location.hasStartedGeofencingAsync(
    BACKGROUND_LOCATION_GEOFENCE_TASK,
  ).catch(() => false);

  return {
    locationTaskRegistered,
    drainTaskRegistered,
    geofenceTaskRegistered,
    locationTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK),
    drainTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK),
    geofenceTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_GEOFENCE_TASK),
  };
}

function buildPermissionDetails(permission: {
  status?: string;
  granted?: boolean;
  canAskAgain?: boolean;
  expires?: string | number;
  ios?: Record<string, unknown>;
  android?: Record<string, unknown>;
}): Record<string, unknown> {
  return {
    status: permission.status,
    granted: permission.granted,
    can_ask_again: permission.canAskAgain,
    expires: permission.expires,
    ...(permission.ios ? { ios: permission.ios } : {}),
    ...(permission.android ? { android: permission.android } : {}),
  };
}

async function getBackgroundExecutionDiagnostics(): Promise<BackgroundExecutionDiagnostics> {
  const [locationServicesEnabled, providerStatus, foregroundPermission, backgroundPermission] =
    await Promise.all([
      Location.hasServicesEnabledAsync().catch(() => undefined),
      Location.getProviderStatusAsync().catch(() => undefined),
      Location.getForegroundPermissionsAsync().catch(() => undefined),
      Location.getBackgroundPermissionsAsync().catch(() => undefined),
    ]);

  return {
    platform: Platform.OS,
    ...(typeof locationServicesEnabled === 'boolean' ? { locationServicesEnabled } : {}),
    ...(providerStatus ? { providerStatus: providerStatus as Record<string, unknown> } : {}),
    ...(foregroundPermission
      ? { foregroundPermissionDetails: buildPermissionDetails(foregroundPermission) }
      : {}),
    ...(backgroundPermission
      ? { backgroundPermissionDetails: buildPermissionDetails(backgroundPermission) }
      : {}),
  };
}

async function getAndroidTaskDiagnostics(): Promise<AndroidTaskDiagnostics> {
  const [taskManagerAvailable, backgroundLocationAvailable, registeredTasks, locationTaskOptions] =
    await Promise.all([
      TaskManager.isAvailableAsync().catch(() => null),
      Location.isBackgroundLocationAvailableAsync().catch(() => null),
      TaskManager.getRegisteredTasksAsync().catch(() => []),
      TaskManager.getTaskOptionsAsync(BACKGROUND_LOCATION_TASK).catch(() => null),
    ]);

  return {
    taskManagerAvailable,
    backgroundLocationAvailable,
    registeredTasks: Array.isArray(registeredTasks)
      ? (registeredTasks as Record<string, unknown>[])
      : [],
    locationTaskOptions: (locationTaskOptions as Record<string, unknown> | null) ?? null,
  };
}

async function ensureAndroidGeofence(anchor: {
  lat: number;
  lon: number;
  capturedAt?: string;
  reason: string;
}): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }
  try {
    await Location.startGeofencingAsync(BACKGROUND_LOCATION_GEOFENCE_TASK, [
      {
        identifier: 'current-place',
        latitude: anchor.lat,
        longitude: anchor.lon,
        radius: ANDROID_GEOFENCE_RADIUS_METERS,
        notifyOnEnter: false,
        notifyOnExit: true,
      },
    ]);
    reportLocationDebugEvent('background_geofence_started', {
      payload: {
        reason: anchor.reason,
        lat: anchor.lat,
        lon: anchor.lon,
        captured_at: anchor.capturedAt,
        radius_meters: ANDROID_GEOFENCE_RADIUS_METERS,
        location_mode: getLocationMode('quiet'),
      },
      recordInHistory: false,
    });
  } catch (error) {
    reportLocationDebugEvent('background_geofence_start_error', {
      error,
      payload: {
        reason: anchor.reason,
        radius_meters: ANDROID_GEOFENCE_RADIUS_METERS,
      },
    });
  }
}

async function stopAndroidGeofence(reason: string): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }
  const started = await Location.hasStartedGeofencingAsync(BACKGROUND_LOCATION_GEOFENCE_TASK).catch(
    () => false,
  );
  if (!started) {
    return;
  }
  try {
    await Location.stopGeofencingAsync(BACKGROUND_LOCATION_GEOFENCE_TASK);
    reportLocationDebugEvent('background_geofence_stopped', {
      payload: {
        reason,
      },
      recordInHistory: false,
    });
  } catch (error) {
    reportLocationDebugEvent('background_geofence_stop_error', {
      error,
      payload: {
        reason,
      },
    });
  }
}

async function applyAndroidLocationTaskMode(
  mode: AndroidCaptureMode,
  reason: string,
): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }
  const desiredOptions = buildBackgroundLocationTaskOptions(mode);
  const alreadyStarted = await Location.hasStartedLocationUpdatesAsync(
    BACKGROUND_LOCATION_TASK,
  ).catch(() => false);
  const currentOptions = (await TaskManager.getTaskOptionsAsync(BACKGROUND_LOCATION_TASK).catch(
    () => null,
  )) as Record<string, unknown> | null;

  if (alreadyStarted && areLocationTaskOptionsEqual(currentOptions, desiredOptions)) {
    return;
  }

  if (alreadyStarted) {
    await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK).catch((error) => {
      reportLocationDebugEvent('background_tracking_stop_before_mode_switch_error', {
        error,
        payload: {
          reason,
          location_mode: getLocationMode(mode),
        },
      });
    });
  }

  reportLocationDebugEvent('background_tracking_mode_start_requested', {
    payload: {
      reason,
      location_mode: getLocationMode(mode),
      task_options: desiredOptions as Record<string, unknown>,
    },
    recordInHistory: false,
  });
  await Location.startLocationUpdatesAsync(BACKGROUND_LOCATION_TASK, desiredOptions);
}

async function transitionAndroidTrackingMode(
  mode: AndroidCaptureMode,
  reason: string,
  anchor?: {
    lat: number;
    lon: number;
    capturedAt: string;
    capturedAtMs: number;
  },
): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }

  trackingModeTransitionInFlight = (trackingModeTransitionInFlight ?? Promise.resolve())
    .catch(() => undefined)
    .then(async () => {
      const currentState = await getAndroidTrackingState();
      if (mode === 'reliable' && getLocationRuntimeState().appState !== 'active') {
        reportLocationDebugEvent('background_tracking_reliable_deferred', {
          payload: {
            reason,
            app_state: getLocationRuntimeState().appState,
            previous_mode: currentState.mode,
            location_mode: getLocationMode(currentState.mode),
            anchor_lat: anchor?.lat ?? currentState.anchorLat,
            anchor_lon: anchor?.lon ?? currentState.anchorLon,
            anchor_captured_at: anchor?.capturedAt ?? currentState.anchorCapturedAt,
          },
          recordInHistory: false,
        });
        if (
          currentState.mode === 'quiet' &&
          typeof currentState.anchorLat === 'number' &&
          typeof currentState.anchorLon === 'number'
        ) {
          await ensureAndroidGeofence({
            lat: currentState.anchorLat,
            lon: currentState.anchorLon,
            capturedAt: currentState.anchorCapturedAt,
            reason: 'reliable_deferred',
          });
        }
        return;
      }

      const nextState: AndroidTrackingState = {
        ...currentState,
        mode,
        updatedAt: new Date().toISOString(),
        reason,
        ...(anchor
          ? {
              anchorLat: anchor.lat,
              anchorLon: anchor.lon,
              anchorCapturedAt: anchor.capturedAt,
              stationarySinceMs:
                mode === 'reliable' ? anchor.capturedAtMs : currentState.stationarySinceMs,
            }
          : {}),
      };

      if (mode === 'reliable') {
        await stopAndroidGeofence(reason);
      }

      await applyAndroidLocationTaskMode(mode, reason);
      await setAndroidTrackingState(nextState);
      reportLocationDebugEvent('background_tracking_mode_changed', {
        payload: {
          reason,
          previous_mode: currentState.mode,
          location_mode: getLocationMode(mode),
          anchor_lat: nextState.anchorLat,
          anchor_lon: nextState.anchorLon,
          anchor_captured_at: nextState.anchorCapturedAt,
          stationary_since_ms: nextState.stationarySinceMs,
        },
        recordInHistory: false,
      });

      if (
        mode === 'quiet' &&
        typeof nextState.anchorLat === 'number' &&
        typeof nextState.anchorLon === 'number'
      ) {
        await ensureAndroidGeofence({
          lat: nextState.anchorLat,
          lon: nextState.anchorLon,
          capturedAt: nextState.anchorCapturedAt,
          reason,
        });
      }
    });

  await trackingModeTransitionInFlight;
}

async function updateAndroidHybridTrackingForSample(sample: {
  lat: number;
  lon: number;
  capturedAt: string;
  capturedAtMs: number;
}): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }

  const state = await getAndroidTrackingState();
  const hasAnchor = typeof state.anchorLat === 'number' && typeof state.anchorLon === 'number';
  if (!hasAnchor) {
    const nextState: AndroidTrackingState = {
      ...state,
      anchorLat: sample.lat,
      anchorLon: sample.lon,
      anchorCapturedAt: sample.capturedAt,
      stationarySinceMs: sample.capturedAtMs,
      updatedAt: new Date().toISOString(),
      reason: 'initial_anchor',
    };
    await setAndroidTrackingState(nextState);
    if (state.mode === 'quiet') {
      await ensureAndroidGeofence({
        lat: sample.lat,
        lon: sample.lon,
        capturedAt: sample.capturedAt,
        reason: 'initial_anchor',
      });
    }
    return;
  }

  const movedFromAnchorMeters = calculateDistanceMeters(
    { lat: state.anchorLat as number, lon: state.anchorLon as number },
    sample,
  );

  if (state.mode === 'quiet') {
    if (movedFromAnchorMeters >= ANDROID_RELIABLE_START_DISTANCE_METERS) {
      await transitionAndroidTrackingMode('reliable', 'movement_detected', sample);
      return;
    }
    return;
  }

  if (movedFromAnchorMeters > ANDROID_STATIONARY_RADIUS_METERS) {
    await setAndroidTrackingState({
      ...state,
      anchorLat: sample.lat,
      anchorLon: sample.lon,
      anchorCapturedAt: sample.capturedAt,
      stationarySinceMs: sample.capturedAtMs,
      updatedAt: new Date().toISOString(),
      reason: 'movement_continued',
    });
    return;
  }

  const stationarySinceMs = state.stationarySinceMs ?? sample.capturedAtMs;
  if (sample.capturedAtMs - stationarySinceMs >= ANDROID_STATIONARY_MIN_MS) {
    await transitionAndroidTrackingMode('quiet', 'stationary_window_reached', {
      ...sample,
    });
    return;
  }

  if (!state.stationarySinceMs) {
    await setAndroidTrackingState({
      ...state,
      stationarySinceMs,
      updatedAt: new Date().toISOString(),
      reason: 'stationary_window_started',
    });
  }
}

async function queueBackgroundLocation(
  sample: BackgroundLocationSample,
  context: BackgroundBatchContext,
): Promise<void> {
  const latitude = Number(sample.coords?.latitude);
  const longitude = Number(sample.coords?.longitude);
  const capturedAtMs = Number(sample.timestamp || Date.now());
  const capturedAt = new Date(capturedAtMs).toISOString();
  const runtimeState = getLocationRuntimeState();
  const debugRequestId = `${context.batchId}:${context.sampleIndex}`;
  const sampleAgeSeconds = Math.max(0, Math.round((Date.now() - capturedAtMs) / 1000));
  const isBufferedFlush =
    runtimeState.appState === 'active' &&
    sampleAgeSeconds >= BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS;
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
          last_accepted_captured_at: new Date(
            lastAcceptedBufferedLocation.capturedAtMs,
          ).toISOString(),
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

  const accuracyRaw = Number(sample.coords?.accuracy);
  const accuracy = Number.isFinite(accuracyRaw) ? Math.round(accuracyRaw * 10) / 10 : undefined;

  await updateAndroidHybridTrackingForSample({
    lat: latitude,
    lon: longitude,
    capturedAt,
    capturedAtMs,
  }).catch((error) => {
    reportLocationDebugEvent('background_tracking_mode_update_error', {
      error,
      payload: {
        captured_at: capturedAt,
        execution_context: context.executionContext,
        app_state: getLocationRuntimeState().appState,
      },
    });
  });

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
    },
    recordInHistory: false,
  });
}

async function ensureAndroidQuietAnchor(reason: string): Promise<AndroidTrackingState> {
  const state = await getAndroidTrackingState();
  if (
    Platform.OS !== 'android' ||
    state.mode !== 'quiet' ||
    (typeof state.anchorLat === 'number' && typeof state.anchorLon === 'number')
  ) {
    return state;
  }

  try {
    const location =
      (await Location.getLastKnownPositionAsync({ maxAge: 30 * 60 * 1000 }).catch(() => null)) ??
      (await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }).catch(
        () => null,
      ));
    if (!location) {
      return state;
    }

    const lat = Number(location.coords.latitude);
    const lon = Number(location.coords.longitude);
    const capturedAtMs = Number(location.timestamp || Date.now());
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      return state;
    }

    const nextState: AndroidTrackingState = {
      ...state,
      anchorLat: lat,
      anchorLon: lon,
      anchorCapturedAt: new Date(capturedAtMs).toISOString(),
      stationarySinceMs: capturedAtMs,
      updatedAt: new Date().toISOString(),
      reason,
    };
    await setAndroidTrackingState(nextState);
    await ensureAndroidGeofence({
      lat,
      lon,
      capturedAt: nextState.anchorCapturedAt,
      reason,
    });
    return nextState;
  } catch (error) {
    reportLocationDebugEvent('background_quiet_anchor_error', {
      error,
      payload: {
        reason,
      },
    });
    return state;
  }
}

async function reconcileAndroidReliableMode(reason: string): Promise<AndroidTrackingState> {
  const state = await getAndroidTrackingState();
  if (
    Platform.OS !== 'android' ||
    state.mode !== 'reliable' ||
    getLocationRuntimeState().appState !== 'active'
  ) {
    return state;
  }

  const location =
    (await Location.getLastKnownPositionAsync({ maxAge: 5 * 60 * 1000 }).catch(() => null)) ??
    (await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }).catch(
      () => null,
    ));
  if (!location || typeof state.anchorLat !== 'number' || typeof state.anchorLon !== 'number') {
    return state;
  }

  const lat = Number(location.coords.latitude);
  const lon = Number(location.coords.longitude);
  const capturedAtMs = Number(location.timestamp || Date.now());
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return state;
  }

  const movedFromAnchorMeters = calculateDistanceMeters(
    { lat: state.anchorLat, lon: state.anchorLon },
    { lat, lon },
  );
  const stationarySinceMs = state.stationarySinceMs ?? capturedAtMs;
  if (
    movedFromAnchorMeters <= ANDROID_STATIONARY_RADIUS_METERS &&
    Date.now() - stationarySinceMs >= ANDROID_STATIONARY_MIN_MS
  ) {
    await transitionAndroidTrackingMode('quiet', reason, {
      lat,
      lon,
      capturedAt: new Date(capturedAtMs).toISOString(),
      capturedAtMs,
    });
    return getAndroidTrackingState();
  }

  return state;
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_GEOFENCE_TASK)) {
  TaskManager.defineTask(
    BACKGROUND_LOCATION_GEOFENCE_TASK,
    async ({ data, error, executionInfo }) => {
      const eventType = (data as { eventType?: number } | undefined)?.eventType;
      const region = (data as { region?: Location.LocationRegion } | undefined)?.region;
      if (error) {
        reportLocationDebugEvent('background_geofence_error', {
          error,
          payload: {
            execution_info_event_id: executionInfo?.eventId,
            location_mode: getLocationMode((await getAndroidTrackingState()).mode),
          },
        });
        return;
      }

      reportLocationDebugEvent('background_geofence_event', {
        payload: {
          event_type: eventType,
          region_identifier: region?.identifier,
          region_latitude: region?.latitude,
          region_longitude: region?.longitude,
          region_radius: region?.radius,
          execution_info_event_id: executionInfo?.eventId,
          location_mode: getLocationMode((await getAndroidTrackingState()).mode),
        },
        recordInHistory: false,
      });

      if (Platform.OS === 'android' && eventType === Location.GeofencingEventType.Exit) {
        await transitionAndroidTrackingMode('reliable', 'geofence_exit');
      }
    },
  );
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_TASK, async ({ data, error, executionInfo }) => {
    const [taskRegistration, executionDiagnostics] = await Promise.all([
      getTaskRegistrationSnapshot().catch(() => ({
        locationTaskRegistered: false,
        drainTaskRegistered: false,
        geofenceTaskRegistered: false,
        locationTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK),
        drainTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK),
        geofenceTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_GEOFENCE_TASK),
      })),
      getBackgroundExecutionDiagnostics().catch(() => ({ platform: Platform.OS })),
    ]);
    const locations = (
      (data as { locations?: BackgroundLocationSample[] } | undefined)?.locations ?? []
    ).filter(Boolean);
    const batchId = `${Date.now()}-${locations.length}`;
    const batchTimestamps = locations
      .map((location) => Number(location.timestamp))
      .filter((timestamp) => Number.isFinite(timestamp) && timestamp > 0)
      .sort((first, second) => first - second);
    const batchFirstCapturedAt = batchTimestamps.length
      ? new Date(batchTimestamps[0]).toISOString()
      : null;
    const batchLastCapturedAt = batchTimestamps.length
      ? new Date(batchTimestamps[batchTimestamps.length - 1]).toISOString()
      : null;
    const oldestSampleAgeSeconds = batchTimestamps.length
      ? Math.max(0, Math.round((Date.now() - batchTimestamps[0]) / 1000))
      : 0;
    const executionContext =
      executionInfo?.appState ?? getLocationRuntimeState().appState ?? 'unknown';

    if (error) {
      reportLocationDebugEvent('background_task_error', {
        error,
        payload: {
          reason: 'task_invocation_error',
          running_in_background: executionContext !== 'active',
          batch_id: batchId,
          sample_count: locations.length,
          batch_first_captured_at: batchFirstCapturedAt,
          batch_last_captured_at: batchLastCapturedAt,
          execution_context: executionContext,
          oldest_sample_age_seconds: oldestSampleAgeSeconds,
          execution_info_event_id: executionInfo?.eventId,
          task_registration: taskRegistration,
          execution_diagnostics: executionDiagnostics,
        },
      });
      return;
    }

    reportLocationDebugEvent('background_task_batch_received', {
      payload: {
        running_in_background: executionContext !== 'active',
        batch_id: batchId,
        sample_count: locations.length,
        batch_first_captured_at: batchFirstCapturedAt,
        batch_last_captured_at: batchLastCapturedAt,
        execution_context: executionContext,
        oldest_sample_age_seconds: oldestSampleAgeSeconds,
        execution_info_event_id: executionInfo?.eventId,
        task_registration: taskRegistration,
        execution_diagnostics: executionDiagnostics,
        will_flush_buffered_samples:
          executionContext === 'active' &&
          oldestSampleAgeSeconds >= BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS,
      },
    });

    if (
      executionContext === 'active' &&
      oldestSampleAgeSeconds >= BACKGROUND_BUFFERED_SAMPLE_MIN_AGE_SECONDS
    ) {
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
          reason: 'no_location_samples',
          request_attempted: false,
          running_in_background: executionContext !== 'active',
          batch_id: batchId,
          sample_count: locations.length,
          batch_first_captured_at: batchFirstCapturedAt,
          batch_last_captured_at: batchLastCapturedAt,
          execution_context: executionContext,
          oldest_sample_age_seconds: oldestSampleAgeSeconds,
          execution_info_event_id: executionInfo?.eventId,
          task_registration: taskRegistration,
          execution_diagnostics: executionDiagnostics,
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
        reportLocationDebugEvent('background_queue_error', {
          message: errorPayload.message,
          error: taskError,
          payload: {
            reason: 'queue_error',
            request_attempted: false,
            batch_id: batchId,
            sample_index: index + 1,
            sample_count: locations.length,
            batch_first_captured_at: batchFirstCapturedAt,
            batch_last_captured_at: batchLastCapturedAt,
            execution_context: executionContext,
            oldest_sample_age_seconds: oldestSampleAgeSeconds,
            status: errorPayload.status,
            auth_expired: errorPayload.authExpired,
            fetch_failed: errorPayload.fetchFailed,
            content_type: errorPayload.contentType,
            response_preview: errorPayload.bodyPreview,
            request_url: errorPayload.requestUrl,
            request_method: errorPayload.requestMethod,
            token_present: errorPayload.tokenPresent,
            api_base_url: API_BASE_URL,
            app_state: getLocationRuntimeState().appState,
            running_in_background: executionContext !== 'active',
            execution_info_event_id: executionInfo?.eventId,
            task_registration: taskRegistration,
            execution_diagnostics: executionDiagnostics,
            ...(errorPayload.authDiagnostics ?? {}),
          },
        });
      }
    }
  });
}

export type BackgroundLocationDebugStatus = {
  locationMode: string;
  androidCaptureMode: AndroidCaptureMode | null;
  configuredDistanceIntervalMeters: number;
  configuredTimeIntervalMs: number;
  foregroundPermission: string;
  backgroundPermission: string;
  locationServicesEnabled: boolean | null;
  backgroundLocationAvailable: boolean | null;
  taskManagerAvailable: boolean | null;
  taskStarted: boolean;
  taskDefined: boolean;
  drainTaskRegistered: boolean;
  drainTaskDefined: boolean;
  geofenceTaskRegistered: boolean;
  geofenceTaskDefined: boolean;
  backgroundTaskStatus: string;
  queuedLocationCount: number;
  oldestQueuedCapturedAt: string | null;
  newestQueuedCapturedAt: string | null;
  providerStatus: Record<string, unknown> | null;
  registeredTasks: Record<string, unknown>[];
  locationTaskOptions: Record<string, unknown> | null;
};

export async function getBackgroundLocationDebugStatus(): Promise<BackgroundLocationDebugStatus> {
  await reconcileAndroidReliableMode('status_reconcile').catch((error) => {
    reportLocationDebugEvent('background_tracking_reconcile_error', {
      error,
    });
  });
  const [
    foregroundPermission,
    backgroundPermission,
    taskStarted,
    drainTaskRegistered,
    backgroundTaskStatus,
    queueSummary,
    trackingState,
    geofenceTaskRegistered,
    locationServicesEnabled,
    providerStatus,
    backgroundLocationAvailable,
    taskManagerAvailable,
    registeredTasks,
    locationTaskOptions,
  ] = await Promise.all([
    Location.getForegroundPermissionsAsync(),
    Location.getBackgroundPermissionsAsync(),
    Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK),
    TaskManager.isTaskRegisteredAsync(BACKGROUND_LOCATION_DRAIN_TASK),
    getBackgroundLocationDrainWorkerStatus(),
    getQueuedBackgroundLocationSummary(),
    getAndroidTrackingState(),
    Location.hasStartedGeofencingAsync(BACKGROUND_LOCATION_GEOFENCE_TASK).catch(() => false),
    Location.hasServicesEnabledAsync().catch(() => null),
    Location.getProviderStatusAsync().catch(() => null),
    Location.isBackgroundLocationAvailableAsync().catch(() => null),
    TaskManager.isAvailableAsync().catch(() => null),
    TaskManager.getRegisteredTasksAsync().catch(() => []),
    TaskManager.getTaskOptionsAsync(BACKGROUND_LOCATION_TASK).catch(() => null),
  ]);

  const resolvedBackgroundTaskStatus = backgroundTaskStatus;

  const androidTaskDiagnostics = await getAndroidTaskDiagnostics().catch(() => ({
    taskManagerAvailable: null,
    backgroundLocationAvailable: null,
    registeredTasks: [],
    locationTaskOptions: null,
  }));

  reportLocationDebugEvent('background_worker_status_snapshot', {
    payload: {
      location_task_started: taskStarted,
      location_task_defined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK),
      drain_task_registered: drainTaskRegistered,
      drain_task_defined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK),
      geofence_task_registered: geofenceTaskRegistered,
      geofence_task_defined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_GEOFENCE_TASK),
      background_task_status: resolvedBackgroundTaskStatus,
      location_services_enabled: locationServicesEnabled,
      background_location_available: backgroundLocationAvailable,
      task_manager_available: taskManagerAvailable,
      provider_status: providerStatus,
      registered_tasks: registeredTasks,
      location_task_options: locationTaskOptions,
      configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
      configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
      queued_location_count: queueSummary.queueSize,
      oldest_queued_captured_at: queueSummary.oldestCapturedAt,
      newest_queued_captured_at: queueSummary.newestCapturedAt,
      location_mode: getLocationMode(trackingState.mode),
      android_capture_mode: Platform.OS === 'android' ? trackingState.mode : null,
      android_anchor_lat: trackingState.anchorLat,
      android_anchor_lon: trackingState.anchorLon,
      android_anchor_captured_at: trackingState.anchorCapturedAt,
      android_stationary_since_ms: trackingState.stationarySinceMs,
      android_geofence_radius_meters: ANDROID_GEOFENCE_RADIUS_METERS,
      android_reliable_start_distance_meters: ANDROID_RELIABLE_START_DISTANCE_METERS,
      android_stationary_radius_meters: ANDROID_STATIONARY_RADIUS_METERS,
      android_stationary_min_ms: ANDROID_STATIONARY_MIN_MS,
    },
    recordInHistory: false,
  });

  if (Platform.OS === 'android') {
    reportLocationDebugEvent('android_background_diagnostics_snapshot', {
      payload: {
        background_task_status: resolvedBackgroundTaskStatus,
        location_task_started: taskStarted,
        drain_task_registered: drainTaskRegistered,
        geofence_task_registered: geofenceTaskRegistered,
        location_services_enabled: locationServicesEnabled,
        provider_status: providerStatus,
        task_manager_available: androidTaskDiagnostics.taskManagerAvailable,
        background_location_available: androidTaskDiagnostics.backgroundLocationAvailable,
        registered_tasks: androidTaskDiagnostics.registeredTasks,
        location_task_options: androidTaskDiagnostics.locationTaskOptions,
        configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
        configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
        location_mode: getLocationMode(trackingState.mode),
        android_capture_mode: trackingState.mode,
        android_anchor_lat: trackingState.anchorLat,
        android_anchor_lon: trackingState.anchorLon,
        android_anchor_captured_at: trackingState.anchorCapturedAt,
        android_stationary_since_ms: trackingState.stationarySinceMs,
      },
      recordInHistory: false,
    });
  }

  return {
    locationMode: getLocationMode(trackingState.mode),
    androidCaptureMode: Platform.OS === 'android' ? trackingState.mode : null,
    configuredDistanceIntervalMeters: BACKGROUND_DISTANCE_INTERVAL_METERS,
    configuredTimeIntervalMs: BACKGROUND_TIME_INTERVAL_MS,
    foregroundPermission: foregroundPermission.status,
    backgroundPermission: backgroundPermission.status,
    locationServicesEnabled,
    backgroundLocationAvailable:
      androidTaskDiagnostics.backgroundLocationAvailable ?? backgroundLocationAvailable,
    taskManagerAvailable: androidTaskDiagnostics.taskManagerAvailable ?? taskManagerAvailable,
    taskStarted,
    taskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK),
    drainTaskRegistered,
    drainTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK),
    geofenceTaskRegistered,
    geofenceTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_GEOFENCE_TASK),
    backgroundTaskStatus: resolvedBackgroundTaskStatus,
    queuedLocationCount: queueSummary.queueSize,
    oldestQueuedCapturedAt: queueSummary.oldestCapturedAt,
    newestQueuedCapturedAt: queueSummary.newestCapturedAt,
    providerStatus,
    registeredTasks: androidTaskDiagnostics.registeredTasks,
    locationTaskOptions: androidTaskDiagnostics.locationTaskOptions,
  };
}

export async function syncBackgroundLocationTracking(enabled: boolean): Promise<void> {
  const taskRegistration = await getTaskRegistrationSnapshot().catch(() => ({
    locationTaskRegistered: false,
    drainTaskRegistered: false,
    geofenceTaskRegistered: false,
    locationTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK),
    drainTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK),
    geofenceTaskDefined: TaskManager.isTaskDefined(BACKGROUND_LOCATION_GEOFENCE_TASK),
  }));
  let trackingState = await getAndroidTrackingState();
  reportLocationDebugEvent('background_tracking_sync_requested', {
    payload: {
      enabled,
      task_registration: taskRegistration,
      location_mode: getLocationMode(trackingState.mode),
      android_capture_mode: Platform.OS === 'android' ? trackingState.mode : null,
      configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
      configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
    },
  });
  const alreadyStarted = await Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);

  if (!enabled) {
    if (alreadyStarted) {
      await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);
      reportLocationDebugEvent('background_tracking_stopped');
    }
    await stopAndroidGeofence('tracking_disabled');
    await unregisterBackgroundLocationDrainTask();
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
      payload: { foreground_status: foregroundStatus, reason: 'foreground_permission_not_granted' },
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
      payload: { background_status: backgroundStatus, reason: 'background_permission_not_granted' },
    });
    return;
  }

  trackingState = await reconcileAndroidReliableMode('tracking_sync_reconcile');
  trackingState = await ensureAndroidQuietAnchor('tracking_sync');
  const taskOptions = buildBackgroundLocationTaskOptions(trackingState.mode);

  if (alreadyStarted) {
    const currentTaskOptions = (await TaskManager.getTaskOptionsAsync(
      BACKGROUND_LOCATION_TASK,
    ).catch(() => null)) as Record<string, unknown> | null;
    const optionsChanged = !areLocationTaskOptionsEqual(currentTaskOptions, taskOptions);

    if (!optionsChanged) {
      await ensureBackgroundLocationDrainTaskRegistered().catch((error) => {
        reportLocationDebugEvent('background_drain_task_register_error', {
          error,
        });
      });
      reportLocationDebugEvent('background_tracking_already_started', {
        payload: {
          platform: Platform.OS,
          location_task_options: currentTaskOptions,
          location_mode: getLocationMode(trackingState.mode),
          android_capture_mode: Platform.OS === 'android' ? trackingState.mode : null,
          configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
          configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
        },
        recordInHistory: false,
      });
      if (
        Platform.OS === 'android' &&
        trackingState.mode === 'quiet' &&
        typeof trackingState.anchorLat === 'number' &&
        typeof trackingState.anchorLon === 'number'
      ) {
        await ensureAndroidGeofence({
          lat: trackingState.anchorLat,
          lon: trackingState.anchorLon,
          capturedAt: trackingState.anchorCapturedAt,
          reason: 'sync_already_started',
        });
      }
      return;
    }

    reportLocationDebugEvent('background_tracking_restart_requested', {
      payload: {
        platform: Platform.OS,
        reason: 'task_options_changed',
        current_task_options: currentTaskOptions,
        desired_task_options: taskOptions as Record<string, unknown>,
        location_mode: getLocationMode(trackingState.mode),
        android_capture_mode: Platform.OS === 'android' ? trackingState.mode : null,
        configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
        configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
      },
      recordInHistory: false,
    });
    await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK).catch((error) => {
      reportLocationDebugEvent('background_tracking_stop_before_restart_error', {
        error,
      });
    });
  }

  try {
    reportLocationDebugEvent('background_tracking_start_requested', {
      payload: {
        platform: Platform.OS,
        task_options: taskOptions as Record<string, unknown>,
        location_mode: getLocationMode(trackingState.mode),
        android_capture_mode: Platform.OS === 'android' ? trackingState.mode : null,
        configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
        configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
      },
      recordInHistory: false,
    });
    await Location.startLocationUpdatesAsync(BACKGROUND_LOCATION_TASK, taskOptions);
  } catch (error) {
    reportLocationDebugEvent('background_tracking_start_error', {
      message:
        error instanceof Error ? error.message : 'Failed to start background location tracking',
      error,
      payload: {
        reason: 'start_location_updates_failed',
        task_registration: taskRegistration,
      },
    });
    throw error;
  }
  await ensureBackgroundLocationDrainTaskRegistered().catch((error) => {
    reportLocationDebugEvent('background_drain_task_register_error', {
      error,
    });
  });
  reportLocationDebugEvent('background_tracking_started', {
    payload: {
      platform: Platform.OS,
      task_options: taskOptions as Record<string, unknown>,
      location_mode: getLocationMode(trackingState.mode),
      android_capture_mode: Platform.OS === 'android' ? trackingState.mode : null,
      configured_distance_interval_meters: BACKGROUND_DISTANCE_INTERVAL_METERS,
      configured_time_interval_ms: BACKGROUND_TIME_INTERVAL_MS,
    },
    recordInHistory: false,
  });
  if (
    Platform.OS === 'android' &&
    trackingState.mode === 'quiet' &&
    typeof trackingState.anchorLat === 'number' &&
    typeof trackingState.anchorLon === 'number'
  ) {
    await ensureAndroidGeofence({
      lat: trackingState.anchorLat,
      lon: trackingState.anchorLon,
      capturedAt: trackingState.anchorCapturedAt,
      reason: 'tracking_started_quiet',
    });
  }
}
