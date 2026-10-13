import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import {
  drainQueuedBackgroundLocations,
  enqueueBackgroundLocationEntry,
} from '@/location/backgroundLocationQueue';
import {
  ensureBackgroundLocationDrainTaskRegistered,
  unregisterBackgroundLocationDrainTask,
} from '@/location/backgroundLocationDrainTask';
import {
  BACKGROUND_LOCATION_GEOFENCE_TASK,
  BACKGROUND_LOCATION_TASK,
} from '@/location/backgroundLocationTaskNames';

import { API_BASE_URL } from '@/api/client';
import { getLocationRuntimeState } from '@/location/runtimeState';
import { hasSharedLocationRuntime } from '@/location/foregroundLocation';

import RuntimeNative from '@/modules/digital-brain-runtime/src';
import { isLocationTrackingEnabled } from '@/location/trackingPreference';

const BACKGROUND_TRACKING_STATE_KEY = 'digitalbrain.backgroundLocationTrackingState';
const BACKGROUND_DISTANCE_INTERVAL_METERS = 5;
const BACKGROUND_TIME_INTERVAL_MS = 5 * 60 * 1000;

const MAX_STORABLE_LOCATION_ACCURACY_METERS = 50;

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

type _BackgroundTaskRegistrationSnapshot = {
  locationTaskRegistered: boolean;
  drainTaskRegistered: boolean;
  geofenceTaskRegistered: boolean;
  locationTaskDefined: boolean;
  drainTaskDefined: boolean;
  geofenceTaskDefined: boolean;
};

type _BackgroundExecutionDiagnostics = {
  platform: string;
  locationServicesEnabled?: boolean;
  providerStatus?: Record<string, unknown>;
  foregroundPermissionDetails?: Record<string, unknown>;
  backgroundPermissionDetails?: Record<string, unknown>;
};

type _AndroidTaskDiagnostics = {
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
  } catch {
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
  } catch {
    /* Best effort; keep the existing recovery path. */
  }
}

async function stopAndroidGeofence(_reason: string): Promise<void> {
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
  } catch {
    /* Best effort; keep the existing recovery path. */
  }
}

async function applyAndroidLocationTaskMode(
  mode: AndroidCaptureMode,
  _reason: string,
): Promise<void> {
  if (Platform.OS !== 'android' || hasSharedLocationRuntime()) {
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
    await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK).catch((_error) => {});
  }

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
  if (Platform.OS !== 'android' || hasSharedLocationRuntime()) {
    return;
  }

  trackingModeTransitionInFlight = (trackingModeTransitionInFlight ?? Promise.resolve())
    .catch(() => undefined)
    .then(async () => {
      const currentState = await getAndroidTrackingState();
      if (mode === 'reliable' && getLocationRuntimeState().appState !== 'active') {
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
      return;
    }
  }

  if (isBufferedFlush && lastAcceptedBufferedLocation) {
    const movedMetersFromBuffered = calculateDistanceMeters(lastAcceptedBufferedLocation, {
      lat: latitude,
      lon: longitude,
    });
    if (capturedAtMs <= lastAcceptedBufferedLocation.capturedAtMs) {
      return;
    }
    if (movedMetersFromBuffered < BACKGROUND_BUFFER_FLUSH_MIN_DISTANCE_METERS) {
      return;
    }
  }

  const accuracyRaw = Number(sample.coords?.accuracy);
  const accuracy = Number.isFinite(accuracyRaw) ? Math.round(accuracyRaw * 10) / 10 : undefined;
  if (accuracy !== undefined && accuracy > MAX_STORABLE_LOCATION_ACCURACY_METERS) {
    return;
  }

  await updateAndroidHybridTrackingForSample({
    lat: latitude,
    lon: longitude,
    capturedAt,
    capturedAtMs,
  }).catch((_error) => {});

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
  } catch {
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
  TaskManager.defineTask(BACKGROUND_LOCATION_GEOFENCE_TASK, async () => {
    // Android location work moved to the native runtime. Keep this task
    // definition for iOS and upgrade cleanup, but never run Android work in JS.
    if (Platform.OS === 'android') return;
  });
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_TASK, async ({ data, error, executionInfo }) => {
    // Android capture and upload are native-only. This definition remains for
    // iOS and safely absorbs stale Android callbacks after upgrade.
    if (Platform.OS === 'android') return;
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

    const executionContext =
      executionInfo?.appState ?? getLocationRuntimeState().appState ?? 'unknown';

    if (error) {
      return;
    }

    if (!locations.length) {
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
      } catch {
        /* Best effort; keep the existing recovery path. */
      }
    }
  });
}

let trackingSyncGeneration = 0;

export async function syncBackgroundLocationTracking(enabled: boolean): Promise<void> {
  const generation = ++trackingSyncGeneration;
  enabled = enabled && (await isLocationTrackingEnabled());
  if (generation !== trackingSyncGeneration) return;
  const sharedRuntime = hasSharedLocationRuntime();
  if (Platform.OS === 'android' && !sharedRuntime) {
    // Android background location and uploads are native-only. Retire old Expo
    // registrations after upgrade instead of silently falling back to JS work.
    await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK).catch(() => undefined);
    await stopAndroidGeofence('native_runtime_unavailable');
    await unregisterBackgroundLocationDrainTask();

    return;
  }
  if (sharedRuntime) {
    // Retire both legacy Android capture registrations, including an in-flight
    // movement callback's old foreground notification. iOS keeps Expo capture.
    if (await Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK)) {
      await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);
    }
    await stopAndroidGeofence('shared_runtime_migration');
    if (generation !== trackingSyncGeneration) return;
    if (!enabled) {
      await RuntimeNative!.setRuntimeLocationEnabled(false);
      await unregisterBackgroundLocationDrainTask();
      return;
    }
    const foreground = await Location.getForegroundPermissionsAsync();
    const foregroundStatus =
      foreground.status === 'granted'
        ? foreground.status
        : (await Location.requestForegroundPermissionsAsync()).status;
    if (generation !== trackingSyncGeneration) return;
    if (foregroundStatus !== 'granted') {
      await RuntimeNative!.setRuntimeLocationEnabled(false);

      return;
    }
    const background = await Location.getBackgroundPermissionsAsync();
    const backgroundStatus =
      background.status === 'granted'
        ? background.status
        : (await Location.requestBackgroundPermissionsAsync()).status;
    if (generation !== trackingSyncGeneration) return;
    if (backgroundStatus !== 'granted') {
      await RuntimeNative!.setRuntimeLocationEnabled(false);

      return;
    }
    // Android's location delivery and upload queue are handled by Kotlin.
    // Remove the prior Expo/Headless-JS drain registration after upgrading.
    await unregisterBackgroundLocationDrainTask();
    if (generation !== trackingSyncGeneration) return;
    const googleWebClientId = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID?.trim();
    if (!googleWebClientId) {
      await RuntimeNative!.setRuntimeLocationEnabled(false);

      return;
    }
    try {
      await RuntimeNative!.configureRuntimeLocationUploader(API_BASE_URL, googleWebClientId);
    } catch {
      await RuntimeNative!.setRuntimeLocationEnabled(false);

      return;
    }
    if (generation !== trackingSyncGeneration) return;
    await RuntimeNative!.setRuntimeLocationEnabled(true);
    // Drain any durable queue left by the prior JS uploader while the app is
    // open. Future Android capture and delivery are native-only.
    void drainQueuedBackgroundLocations('manual').catch((_error) => {});

    return;
  }

  let trackingState = await getAndroidTrackingState();

  const alreadyStarted = await Location.hasStartedLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);

  if (!enabled) {
    if (alreadyStarted) {
      await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK);
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
    return;
  }

  const backgroundPermission = await Location.getBackgroundPermissionsAsync();
  const backgroundStatus =
    backgroundPermission.status === 'granted'
      ? backgroundPermission.status
      : (await Location.requestBackgroundPermissionsAsync()).status;
  if (backgroundStatus !== 'granted') {
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
      await ensureBackgroundLocationDrainTaskRegistered().catch((_error) => {});

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

    await Location.stopLocationUpdatesAsync(BACKGROUND_LOCATION_TASK).catch((_error) => {});
  }

  try {
    await Location.startLocationUpdatesAsync(BACKGROUND_LOCATION_TASK, taskOptions);
  } catch (error) {
    throw error;
  }
  await ensureBackgroundLocationDrainTaskRegistered().catch((_error) => {});

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
