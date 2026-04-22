import AsyncStorage from '@react-native-async-storage/async-storage';

export type LocationDebugEvent = {
  at: string;
  eventName: string;
  message?: string;
  error?: string;
  payload?: Record<string, unknown>;
  successCountSincePreviousFailure?: number;
};

export type LocationDebugSnapshot = {
  lastEventAt?: string;
  lastEventName?: string;
  lastMessage?: string;
  lastError?: string;
  lastPayload?: Record<string, unknown>;
  lastSuccessAt?: string;
  totalSuccessCount?: number;
  successCountSinceLastFailure?: number;
  recentFailures?: LocationDebugEvent[];
  eventLog?: LocationDebugEvent[];
};

type Listener = (snapshot: LocationDebugSnapshot) => void;

const LOCATION_DEBUG_SNAPSHOT_KEY = 'digitalbrain.locationDebugSnapshot';
const MAX_LOCATION_DEBUG_FAILURES = 100;
const MAX_LOCATION_DEBUG_LOG_EVENTS = 500;

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

export function buildLocationDebugLogText(current: LocationDebugSnapshot = snapshot): string {
  const lines = [
    'Digital Brain Mobile Location Debug Log',
    `Generated: ${new Date().toISOString()}`,
    `Last event: ${current.lastEventName ?? 'none'}`,
    `Last event at: ${current.lastEventAt ?? 'none'}`,
    `Last success at: ${current.lastSuccessAt ?? 'none'}`,
    `Total successes: ${current.totalSuccessCount ?? 0}`,
    `Successes since last failure: ${current.successCountSinceLastFailure ?? 0}`,
    '',
    'Event log:',
  ];

  for (const event of current.eventLog ?? []) {
    lines.push(`[${event.at}] ${event.eventName}`);
    lines.push(`  Message: ${event.message ?? 'none'}`);
    lines.push(`  Error: ${event.error ?? 'none'}`);
    lines.push(`  Successes before failure: ${event.successCountSincePreviousFailure ?? 0}`);
    lines.push(`  Payload: ${event.payload ? JSON.stringify(event.payload) : 'none'}`);
  }

  return lines.join('\n');
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
      recentFailures: Array.isArray(parsed.recentFailures) ? parsed.recentFailures : [],
      eventLog: Array.isArray(parsed.eventLog) ? parsed.eventLog : [],
    };
    notify();
  } catch (error) {
    console.warn('[location-debug] failed to hydrate snapshot', error);
  }

  return getLocationDebugSnapshot();
}

function isSuccessEvent(eventName: string): boolean {
  return eventName.endsWith('_success');
}

function isFailureEvent(eventName: string): boolean {
  return (
    eventName.endsWith('_error') ||
    eventName.endsWith('_blocked') ||
    eventName.endsWith('_invalid') ||
    eventName === 'background_sync_skipped' ||
    eventName === 'foreground_permission_denied' ||
    eventName === 'background_task_empty'
  );
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
    recordInHistory?: boolean;
  },
): void {
  const eventAt = new Date().toISOString();
  const normalizedError =
    details?.error instanceof Error ? details.error.message : details?.error ? String(details.error) : undefined;
  const successCountSinceLastFailure = snapshot.successCountSinceLastFailure ?? 0;
  const nextEvent: LocationDebugEvent = {
    at: eventAt,
    eventName,
    message: details?.message,
    error: normalizedError,
    payload: details?.payload,
    successCountSincePreviousFailure: successCountSinceLastFailure,
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
    eventLog: [nextEvent, ...(snapshot.eventLog ?? [])].slice(0, MAX_LOCATION_DEBUG_LOG_EVENTS),
  };

  if (isSuccessEvent(eventName)) {
    snapshot = {
      ...snapshot,
      lastSuccessAt: eventAt,
      totalSuccessCount: (snapshot.totalSuccessCount ?? 0) + 1,
      successCountSinceLastFailure: successCountSinceLastFailure + 1,
      lastError: undefined,
    };
  } else if (details?.recordInHistory ?? isFailureEvent(eventName)) {
    snapshot = {
      ...snapshot,
      successCountSinceLastFailure: 0,
      recentFailures: [nextEvent, ...(snapshot.recentFailures ?? [])].slice(0, MAX_LOCATION_DEBUG_FAILURES),
    };
  }

  notify();
  void AsyncStorage.setItem(LOCATION_DEBUG_SNAPSHOT_KEY, JSON.stringify(snapshot)).catch((error) => {
    console.warn('[location-debug] failed to persist snapshot', error);
  });
}
