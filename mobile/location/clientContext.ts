import * as Location from 'expo-location';

import { apiFetch, API_BASE_URL, getAuthRequestContext } from '@/api/client';
import { reportLocationDebugEvent } from '@/location/debugState';
import { getLocationRuntimeState } from '@/location/runtimeState';

export type ClientLocationContext = {
  lat: number;
  lon: number;
  accuracy_m?: number;
  captured_at: string;
  source: 'expo_location';
};

export type ClientContext = {
  timezone?: string;
  locale?: string;
  location?: ClientLocationContext;
};

let cachedClientContext: ClientContext | null = null;
let locationRequestInFlight = false;
let locationSyncInFlight = false;
let lastSyncedLocation: ClientLocationContext | null = null;

const LOCATION_SYNC_MIN_DISTANCE_METERS = 20;

function calculateDistanceMeters(
  first: Pick<ClientLocationContext, 'lat' | 'lon'>,
  second: Pick<ClientLocationContext, 'lat' | 'lon'>,
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

function getBaseClientContext(): ClientContext {
  const options = Intl.DateTimeFormat().resolvedOptions();
  return {
    timezone: options.timeZone || undefined,
    locale: options.locale || undefined,
  };
}

function requestLocationInBackground(): void {
  if (locationRequestInFlight) {
    reportLocationDebugEvent('foreground_location_skipped', {
      message: 'Location request already in flight',
    });
    return;
  }

  locationRequestInFlight = true;
  reportLocationDebugEvent('foreground_location_requested');
  void (async () => {
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (permission.status !== 'granted') {
        reportLocationDebugEvent('foreground_permission_denied', {
          message: 'Foreground location permission not granted',
        });
        return;
      }

      const position = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });

      const lat = Number(position.coords.latitude);
      const lon = Number(position.coords.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        reportLocationDebugEvent('foreground_location_invalid', {
          message: 'Invalid foreground coordinates',
          payload: { lat, lon },
        });
        return;
      }

      const rawAccuracy = Number(position.coords.accuracy);
      const accuracy = Number.isFinite(rawAccuracy)
        ? Math.round(rawAccuracy * 10) / 10
        : undefined;

      const capturedAt = new Date(position.timestamp || Date.now()).toISOString();
      cachedClientContext = {
        ...(cachedClientContext ?? getBaseClientContext()),
        location: {
          lat,
          lon,
          accuracy_m: accuracy,
          captured_at: capturedAt,
          source: 'expo_location',
        },
      };
      reportLocationDebugEvent('foreground_location_captured', {
        payload: {
          lat,
          lon,
          accuracy_m: accuracy,
          captured_at: capturedAt,
        },
      });
      syncLocationToBackend();
    } catch (error) {
      reportLocationDebugEvent('foreground_location_error', {
        error,
      });
      // Best-effort only; keep timezone/locale context when location fails.
    } finally {
      locationRequestInFlight = false;
    }
  })();
}

function syncLocationToBackend(): void {
  const location = cachedClientContext?.location;
  if (!location || locationSyncInFlight) {
    if (!location) {
      reportLocationDebugEvent('location_sync_skipped', {
        message: 'No location in client context',
      });
    }
    return;
  }

  if (lastSyncedLocation) {
    const movedMeters = calculateDistanceMeters(location, lastSyncedLocation);
    if (movedMeters < LOCATION_SYNC_MIN_DISTANCE_METERS) {
      reportLocationDebugEvent('location_sync_skipped', {
        message: `Movement below threshold (${Math.round(movedMeters)}m)`,
        payload: {
          moved_meters: Math.round(movedMeters),
          threshold_meters: LOCATION_SYNC_MIN_DISTANCE_METERS,
        },
      });
      return;
    }
  }

  const signature = `${location.lat}:${location.lon}`;
  if (lastSyncedLocation && signature === `${lastSyncedLocation.lat}:${lastSyncedLocation.lon}`) {
    reportLocationDebugEvent('location_sync_skipped', {
      message: 'Same coordinates already synced',
      payload: { lat: location.lat, lon: location.lon },
    });
    return;
  }

  locationSyncInFlight = true;
  void (async () => {
    const runtimeState = getLocationRuntimeState();
    try {
      const { token, authDiagnostics } = await getAuthRequestContext();
      if (!token) {
        reportLocationDebugEvent('location_sync_skipped', {
          message: 'Auth token not ready for foreground sync',
          payload: {
            api_base_url: API_BASE_URL,
            app_state: runtimeState.appState,
            captured_at: location.captured_at,
            ...authDiagnostics,
          },
        });
        return;
      }

      reportLocationDebugEvent('location_sync_attempt', {
        payload: {
          lat: location.lat,
          lon: location.lon,
          captured_at: location.captured_at,
          api_base_url: API_BASE_URL,
          app_state: runtimeState.appState,
          last_app_state_change_at: runtimeState.lastAppStateChangeAt,
          ...authDiagnostics,
        },
      });

      await apiFetch('/mobile/location', {
        method: 'POST',
        token,
        body: JSON.stringify({
          ...location,
          timezone: cachedClientContext?.timezone,
        }),
      });

      lastSyncedLocation = { ...location };
      reportLocationDebugEvent('location_sync_success', {
        payload: {
          lat: location.lat,
          lon: location.lon,
          captured_at: location.captured_at,
          ...authDiagnostics,
        },
      });
    } catch (error) {
      const errorWithMeta = error as Error & {
        status?: number;
        authExpired?: boolean;
        contentType?: string;
        bodyPreview?: string;
        requestUrl?: string;
        tokenPresent?: boolean;
        authDiagnostics?: Record<string, unknown>;
      };
      reportLocationDebugEvent('location_sync_error', {
        message: errorWithMeta.message,
        error,
        payload: {
          api_base_url: API_BASE_URL,
          app_state: runtimeState.appState,
          captured_at: location.captured_at,
          status: errorWithMeta.status,
          auth_expired: errorWithMeta.authExpired,
          content_type: errorWithMeta.contentType,
          response_preview: errorWithMeta.bodyPreview,
          request_url: errorWithMeta.requestUrl,
          token_present: errorWithMeta.tokenPresent,
          ...(errorWithMeta.authDiagnostics ?? {}),
        },
      });
    } finally {
      locationSyncInFlight = false;
    }
  })();
}

export function primeClientContext(): void {
  if (!cachedClientContext) {
    cachedClientContext = getBaseClientContext();
  }
  requestLocationInBackground();
}

export function getClientContext(): ClientContext {
  if (!cachedClientContext) {
    primeClientContext();
  }
  return {
    ...(cachedClientContext ?? {}),
    location: cachedClientContext?.location
      ? { ...cachedClientContext.location }
      : undefined,
  };
}
