export type ClientLocationContext = {
  lat: number;
  lon: number;
  accuracy_m?: number;
  captured_at: string;
  source: 'mobile_geolocation';
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

  const geolocation = (globalThis.navigator as any)?.geolocation;
  if (!geolocation || typeof geolocation.getCurrentPosition !== 'function') {
    return;
  }

  locationRequestInFlight = true;
  geolocation.getCurrentPosition(
    (position: any) => {
      const lat = roundCoordinate(Number(position?.coords?.latitude));
      const lon = roundCoordinate(Number(position?.coords?.longitude));
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        locationRequestInFlight = false;
        return;
      }

      const rawAccuracy = Number(position?.coords?.accuracy);
      const accuracy = Number.isFinite(rawAccuracy)
        ? Math.round(rawAccuracy * 10) / 10
        : undefined;

      const capturedAt = new Date(position?.timestamp || Date.now()).toISOString();
      cachedClientContext = {
        ...(cachedClientContext ?? getBaseClientContext()),
        location: {
          lat,
          lon,
          accuracy_m: accuracy,
          captured_at: capturedAt,
          source: 'mobile_geolocation',
        },
      };
      locationRequestInFlight = false;
    },
    () => {
      locationRequestInFlight = false;
    },
    {
      enableHighAccuracy: false,
      timeout: 10000,
      maximumAge: 5 * 60 * 1000,
    }
  );
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
