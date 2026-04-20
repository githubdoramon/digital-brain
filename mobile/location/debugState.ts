import AsyncStorage from '@react-native-async-storage/async-storage';

export type LocationDebugEvent = {
  at: string;
  eventName: string;
  message?: string;
  error?: string;
  payload?: Record<string, unknown>;
};

export type LocationDebugSnapshot = {
  lastEventAt?: string;
  lastEventName?: string;
  lastMessage?: string;
  lastError?: string;
  lastPayload?: Record<string, unknown>;
  recentEvents?: LocationDebugEvent[];
};

type Listener = (snapshot: LocationDebugSnapshot) => void;

const LOCATION_DEBUG_SNAPSHOT_KEY = 'digitalbrain.locationDebugSnapshot';
const MAX_LOCATION_DEBUG_EVENTS = 20;

let snapshot: LocationDebugSnapshot = {};
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) {
    listener(snapshot);
  }
}

export function getLocationDebugSnapshot(): LocationDebugSnapshot {
  return { ...snapshot };
}

export async function hydrateLocationDebugSnapshot(): Promise<LocationDebugSnapshot> {
  try {
    const stored = await AsyncStorage.getItem(LOCATION_DEBUG_SNAPSHOT_KEY);
    if (!stored) {
      return getLocationDebugSnapshot();
    }

    const parsed = JSON.parse(stored) as LocationDebugSnapshot;
    snapshot = {
      ...snapshot,
      ...parsed,
    };
    notify();
  } catch (error) {
    console.warn('[location-debug] failed to hydrate snapshot', error);
  }

  return getLocationDebugSnapshot();
}

export function subscribeLocationDebug(listener: Listener): () => void {
  listeners.add(listener);
  listener(getLocationDebugSnapshot());
  return () => {
    listeners.delete(listener);
  };
}

export function reportLocationDebugEvent(
  eventName: string,
  details?: {
    message?: string;
    error?: unknown;
    payload?: Record<string, unknown>;
  },
): void {
  const eventAt = new Date().toISOString();
  const normalizedError =
    details?.error instanceof Error ? details.error.message : details?.error ? String(details.error) : undefined;
  const nextEvent: LocationDebugEvent = {
    at: eventAt,
    eventName,
    message: details?.message,
    error: normalizedError,
    payload: details?.payload,
  };

  console.info('[location-debug]', eventName, {
    message: details?.message,
    payload: details?.payload,
    error: normalizedError,
  });
  snapshot = {
    ...snapshot,
    lastEventAt: eventAt,
    lastEventName: eventName,
    lastMessage: details?.message,
    lastPayload: details?.payload,
    lastError: normalizedError,
    recentEvents: [nextEvent, ...(snapshot.recentEvents ?? [])].slice(0, MAX_LOCATION_DEBUG_EVENTS),
  };
  notify();
  void AsyncStorage.setItem(LOCATION_DEBUG_SNAPSHOT_KEY, JSON.stringify(snapshot)).catch((error) => {
    console.warn('[location-debug] failed to persist snapshot', error);
  });
}
