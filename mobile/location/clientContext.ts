import * as Location from 'expo-location';

import { apiFetch } from '@/api/client';

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

function roundCoordinate(value: number): number {
  return Math.round(value * 1000) / 1000;
}

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
    return;
  }

  locationRequestInFlight = true;
  void (async () => {
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (permission.status !== 'granted') {
        return;
      }

      const position = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });

      const lat = roundCoordinate(Number(position.coords.latitude));
      const lon = roundCoordinate(Number(position.coords.longitude));
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
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
      syncLocationToBackend();
    } catch {
      // Best-effort only; keep timezone/locale context when location fails.
    } finally {
      locationRequestInFlight = false;
    }
  })();
}

function syncLocationToBackend(): void {
  const location = cachedClientContext?.location;
  if (!location || locationSyncInFlight) {
    return;
  }

  if (lastSyncedLocation) {
    const movedMeters = calculateDistanceMeters(location, lastSyncedLocation);
    if (movedMeters < LOCATION_SYNC_MIN_DISTANCE_METERS) {
      return;
    }
  }

  const signature = `${location.lat}:${location.lon}`;
  if (lastSyncedLocation && signature === `${lastSyncedLocation.lat}:${lastSyncedLocation.lon}`) {
    return;
  }

  locationSyncInFlight = true;
  void apiFetch('/mobile/location', {
    method: 'POST',
    body: JSON.stringify({
      ...location,
      timezone: cachedClientContext?.timezone,
    }),
  })
    .then(() => {
      lastSyncedLocation = { ...location };
    })
    .catch(() => {
      // Best-effort sync; location context should still be available for ask flows.
    })
    .finally(() => {
      locationSyncInFlight = false;
    });
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
