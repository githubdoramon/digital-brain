import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

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
const LOCATION_DEBUG_LOG_FILE_NAME = 'digital-brain-location-debug-log.jsonl';
const LOCATION_DEBUG_LOG_URI = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}${LOCATION_DEBUG_LOG_FILE_NAME}`;

let snapshot: LocationDebugSnapshot = {};
const listeners = new Set<Listener>();
let fileWriteChain: Promise<void> = Promise.resolve();

function notify(): void {
  for (const listener of listeners) {
    listener(snapshot);
  }
}

export function getLocationDebugSnapshot(): LocationDebugSnapshot {
  return { ...snapshot };
}

function isBackgroundRelevantPayload(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) {
    return false;
  }

  if (payload.running_in_background === true) {
    return true;
  }

  return payload.app_state === 'background' || payload.execution_context === 'background';
}

export function isBackgroundRelevantLocationEvent(event: LocationDebugEvent): boolean {
  return (
    event.eventName.startsWith('background_task_') ||
    event.eventName.startsWith('background_queue_') ||
    event.eventName.startsWith('background_sync_') ||
    event.eventName.startsWith('background_buffered_') ||
    event.eventName.startsWith('background_auth_refresh_') ||
    event.eventName === 'background_location_invalid' ||
    isBackgroundRelevantPayload(event.payload)
  );
}

function buildLogLines(events: LocationDebugEvent[]): string[] {
  const lines = ['Event log:'];

  for (const event of events) {
    lines.push(`[${event.at}] ${event.eventName}`);
    lines.push(`  Message: ${event.message ?? 'none'}`);
    lines.push(`  Error: ${event.error ?? 'none'}`);
    lines.push(`  Successes before failure: ${event.successCountSincePreviousFailure ?? 0}`);
    lines.push(`  Payload: ${event.payload ? JSON.stringify(event.payload) : 'none'}`);
  }

  return lines;
}

export function buildLocationDebugLogText(
  current: LocationDebugSnapshot = snapshot,
  options?: { backgroundOnly?: boolean },
): string {
  const eventLog = current.eventLog ?? [];
  const filteredEvents = options?.backgroundOnly
    ? eventLog.filter(isBackgroundRelevantLocationEvent)
    : eventLog;
  const lastRelevantEvent = filteredEvents[0];
  const lines = [
    options?.backgroundOnly ? 'Digital Brain Mobile Background Location Debug Log' : 'Digital Brain Mobile Location Debug Log',
    `Generated: ${new Date().toISOString()}`,
    `Last event: ${lastRelevantEvent?.eventName ?? current.lastEventName ?? 'none'}`,
    `Last event at: ${lastRelevantEvent?.at ?? current.lastEventAt ?? 'none'}`,
    `Last success at: ${current.lastSuccessAt ?? 'none'}`,
    `Total successes: ${current.totalSuccessCount ?? 0}`,
    `Successes since last failure: ${current.successCountSinceLastFailure ?? 0}`,
    `Background-only export: ${options?.backgroundOnly ? 'yes' : 'no'}`,
    '',
    `Event count: ${filteredEvents.length}`,
  ];

  lines.push('', ...buildLogLines(filteredEvents));

  return lines.join('\n');
}

async function ensureLocationDebugLogDirectory(): Promise<void> {
  const baseDirectory = FileSystem.documentDirectory ?? FileSystem.cacheDirectory;
  if (!baseDirectory) {
    return;
  }
  await FileSystem.makeDirectoryAsync(baseDirectory, { intermediates: true }).catch(() => undefined);
}

function persistLocationDebugEventToFile(event: LocationDebugEvent): void {
  fileWriteChain = fileWriteChain
    .catch(() => undefined)
    .then(async () => {
      await ensureLocationDebugLogDirectory();
      await FileSystem.writeAsStringAsync(LOCATION_DEBUG_LOG_URI, `${JSON.stringify(event)}\n`, {
        encoding: FileSystem.EncodingType.UTF8,
        append: true,
      });
    })
    .catch((error) => {
      console.warn('[location-debug] failed to append log file', error);
    });
}

export function getLocationDebugLogFileName(): string {
  return LOCATION_DEBUG_LOG_FILE_NAME;
}

export async function getLocationDebugLogInfo(): Promise<{ exists: boolean; sizeBytes: number }> {
  const info = await FileSystem.getInfoAsync(LOCATION_DEBUG_LOG_URI);
  return {
    exists: info.exists,
    sizeBytes: info.exists ? (info.size ?? 0) : 0,
  };
}

export async function readLocationDebugLogText(options?: { backgroundOnly?: boolean }): Promise<string> {
  const info = await FileSystem.getInfoAsync(LOCATION_DEBUG_LOG_URI);
  if (!info.exists) {
    return buildLocationDebugLogText(snapshot, options);
  }

  const raw = await FileSystem.readAsStringAsync(LOCATION_DEBUG_LOG_URI, {
    encoding: FileSystem.EncodingType.UTF8,
  });
  const events = raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line) as LocationDebugEvent;
      } catch {
        return null;
      }
    })
    .filter((event): event is LocationDebugEvent => Boolean(event));
  const filteredEvents = options?.backgroundOnly
    ? events.filter(isBackgroundRelevantLocationEvent)
    : events;
  const syntheticSnapshot: LocationDebugSnapshot = {
    ...snapshot,
    eventLog: filteredEvents,
  };
  return buildLocationDebugLogText(syntheticSnapshot, options);
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
  persistLocationDebugEventToFile(nextEvent);
  void AsyncStorage.setItem(LOCATION_DEBUG_SNAPSHOT_KEY, JSON.stringify(snapshot)).catch((error) => {
    console.warn('[location-debug] failed to persist snapshot', error);
  });
}
