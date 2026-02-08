import * as Location from 'expo-location';

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

function roundCoordinate(value: number): number {
  return Math.round(value * 1000) / 1000;
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
    } catch {
      // Best-effort only; keep timezone/locale context when location fails.
    } finally {
      locationRequestInFlight = false;
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
