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
    event.eventName.startsWith('background_tracking_') ||
    event.eventName.startsWith('background_geofence_') ||
    event.eventName.startsWith('background_worker_') ||
    event.eventName.startsWith('background_queue_') ||
    event.eventName.startsWith('background_sync_') ||
    event.eventName.startsWith('background_buffered_') ||
    event.eventName.startsWith('background_auth_refresh_') ||
    event.eventName.startsWith('android_background_') ||
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

function getLatestLocationDebugEvent(events: LocationDebugEvent[]): LocationDebugEvent | undefined {
  return events.reduce<LocationDebugEvent | undefined>((latest, event) => {
    if (!latest) {
      return event;
    }
    return Date.parse(event.at) > Date.parse(latest.at) ? event : latest;
  }, undefined);
}

function getPayloadString(
  payload: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = payload?.[key];
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function getPayloadNumber(
  payload: Record<string, unknown> | undefined,
  key: string,
): number | undefined {
  const value = payload?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function isFailureDebugEvent(event: LocationDebugEvent): boolean {
  return Boolean(event.error) || isFailureEvent(event.eventName);
}

function buildBackgroundSummaryLines(events: LocationDebugEvent[]): string[] {
  const chronologicalEvents = [...events].sort(
    (first, second) => Date.parse(first.at) - Date.parse(second.at),
  );
  let estimatedQueueSize: number | undefined;
  let oldestQueuedCapturedAt: string | undefined;
  let newestQueuedCapturedAt: string | undefined;
  let lastDrainStarted: LocationDebugEvent | undefined;
  let lastDrainFinished: LocationDebugEvent | undefined;
  let lastUploadSuccess: LocationDebugEvent | undefined;
  let lastCaptureBatch: LocationDebugEvent | undefined;
  let lastQueueReady: LocationDebugEvent | undefined;
  let lastTrackingEvent: LocationDebugEvent | undefined;
  let lastGeofenceEvent: LocationDebugEvent | undefined;
  let lastStatusSnapshot: LocationDebugEvent | undefined;
  let lastFailure: LocationDebugEvent | undefined;

  for (const event of chronologicalEvents) {
    if (isFailureDebugEvent(event)) {
      lastFailure = event;
    }

    if (event.eventName === 'background_task_batch_received') {
      lastCaptureBatch = event;
    } else if (event.eventName.startsWith('background_tracking_')) {
      lastTrackingEvent = event;
    } else if (event.eventName.startsWith('background_geofence_')) {
      lastGeofenceEvent = event;
    } else if (
      event.eventName === 'background_worker_status_snapshot' ||
      event.eventName === 'android_background_diagnostics_snapshot'
    ) {
      lastStatusSnapshot = event;
    } else if (event.eventName === 'background_queue_drain_started') {
      lastDrainStarted = event;
      estimatedQueueSize = getPayloadNumber(event.payload, 'queue_size') ?? estimatedQueueSize;
    } else if (event.eventName === 'background_queue_drain_finished') {
      lastDrainFinished = event;
      estimatedQueueSize =
        getPayloadNumber(event.payload, 'remaining_queue_size') ?? estimatedQueueSize;
      if (estimatedQueueSize === 0) {
        oldestQueuedCapturedAt = undefined;
        newestQueuedCapturedAt = undefined;
      }
    } else if (event.eventName === 'background_queue_enqueued') {
      const capturedAt = getPayloadString(event.payload, 'captured_at');
      estimatedQueueSize = getPayloadNumber(event.payload, 'queue_size') ?? estimatedQueueSize;
      if (capturedAt) {
        if (
          !oldestQueuedCapturedAt ||
          Date.parse(capturedAt) < Date.parse(oldestQueuedCapturedAt)
        ) {
          oldestQueuedCapturedAt = capturedAt;
        }
        if (
          !newestQueuedCapturedAt ||
          Date.parse(capturedAt) > Date.parse(newestQueuedCapturedAt)
        ) {
          newestQueuedCapturedAt = capturedAt;
        }
      }
    } else if (event.eventName === 'background_sync_success') {
      lastUploadSuccess = event;
      estimatedQueueSize = getPayloadNumber(event.payload, 'queue_size') ?? estimatedQueueSize;
      const capturedAt = getPayloadString(event.payload, 'captured_at');
      if (capturedAt && capturedAt === oldestQueuedCapturedAt) {
        oldestQueuedCapturedAt = undefined;
      }
      if (estimatedQueueSize === 0) {
        oldestQueuedCapturedAt = undefined;
        newestQueuedCapturedAt = undefined;
      }
    } else if (event.eventName === 'background_queue_ready_for_drain') {
      lastQueueReady = event;
      const capturedAt = getPayloadString(event.payload, 'captured_at');
      if (capturedAt) {
        newestQueuedCapturedAt =
          newestQueuedCapturedAt && Date.parse(newestQueuedCapturedAt) > Date.parse(capturedAt)
            ? newestQueuedCapturedAt
            : capturedAt;
        oldestQueuedCapturedAt =
          oldestQueuedCapturedAt && Date.parse(oldestQueuedCapturedAt) < Date.parse(capturedAt)
            ? oldestQueuedCapturedAt
            : capturedAt;
      }
    }
  }

  const lastDrainFinishedQueueSize = getPayloadNumber(
    lastDrainFinished?.payload,
    'remaining_queue_size',
  );
  const lastDrainStartedQueueSize = getPayloadNumber(lastDrainStarted?.payload, 'queue_size');
  return [
    'Capture service:',
    `Last capture batch at: ${lastCaptureBatch?.at ?? 'none'}`,
    `Last capture batch samples: ${getPayloadNumber(lastCaptureBatch?.payload, 'sample_count') ?? 'unknown'}`,
    `Last queued capture at: ${getPayloadString(lastQueueReady?.payload, 'captured_at') ?? 'none'}`,
    `Last tracking event: ${lastTrackingEvent?.eventName ?? 'none'}`,
    `Last tracking event at: ${lastTrackingEvent?.at ?? 'none'}`,
    `Last geofence event: ${lastGeofenceEvent?.eventName ?? 'none'}`,
    `Last geofence event at: ${lastGeofenceEvent?.at ?? 'none'}`,
    `Last status snapshot at: ${lastStatusSnapshot?.at ?? 'none'}`,
    `Last status location mode: ${getPayloadString(lastStatusSnapshot?.payload, 'location_mode') ?? 'unknown'}`,
    `Last status queued locations: ${getPayloadNumber(lastStatusSnapshot?.payload, 'queued_location_count') ?? 'unknown'}`,
    '',
    'Drain/upload service:',
    `Estimated queued locations: ${estimatedQueueSize ?? 'unknown'}`,
    `Oldest estimated queued capture: ${oldestQueuedCapturedAt ?? 'none'}`,
    `Newest estimated queued capture: ${newestQueuedCapturedAt ?? 'none'}`,
    `Last drain started at: ${lastDrainStarted?.at ?? 'none'}`,
    `Last drain started queue size: ${lastDrainStartedQueueSize ?? 'unknown'}`,
    `Last drain finished at: ${lastDrainFinished?.at ?? 'none'}`,
    `Last drain finished remaining queue: ${lastDrainFinishedQueueSize ?? 'unknown'}`,
    `Last upload success at: ${lastUploadSuccess?.at ?? 'none'}`,
    `Last uploaded capture: ${getPayloadString(lastUploadSuccess?.payload, 'captured_at') ?? 'none'}`,
    `Last failure event: ${lastFailure?.eventName ?? 'none'}`,
    `Last failure at: ${lastFailure?.at ?? 'none'}`,
    `Last failure reason: ${getPayloadString(lastFailure?.payload, 'reason') ?? lastFailure?.message ?? lastFailure?.error ?? 'none'}`,
  ];
}

export function buildLocationDebugLogText(
  current: LocationDebugSnapshot = snapshot,
  options?: { backgroundOnly?: boolean },
): string {
  const eventLog = current.eventLog ?? [];
  const filteredEvents = options?.backgroundOnly
    ? eventLog.filter(isBackgroundRelevantLocationEvent)
    : eventLog;
  const lastRelevantEvent = getLatestLocationDebugEvent(filteredEvents);
  const lines = [
    options?.backgroundOnly
      ? 'Digital Brain Mobile Background Location Debug Log'
      : 'Digital Brain Mobile Location Debug Log',
    `Generated: ${new Date().toISOString()}`,
    `Last event: ${lastRelevantEvent?.eventName ?? current.lastEventName ?? 'none'}`,
    `Last event at: ${lastRelevantEvent?.at ?? current.lastEventAt ?? 'none'}`,
    `Last success at: ${current.lastSuccessAt ?? 'none'}`,
    `Total successes: ${current.totalSuccessCount ?? 0}`,
    `Successes since last failure: ${current.successCountSinceLastFailure ?? 0}`,
    `Background-only export: ${options?.backgroundOnly ? 'yes' : 'no'}`,
    '',
    'Queue/drain summary:',
    ...buildBackgroundSummaryLines(filteredEvents),
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
  await FileSystem.makeDirectoryAsync(baseDirectory, { intermediates: true }).catch(
    () => undefined,
  );
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

export async function readLocationDebugLogText(options?: {
  backgroundOnly?: boolean;
}): Promise<string> {
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
  const eventKeys = new Set(events.map((event) => `${event.at}:${event.eventName}`));
  const mergedEvents = [
    ...events,
    ...(snapshot.eventLog ?? []).filter((event) => {
      const key = `${event.at}:${event.eventName}`;
      if (eventKeys.has(key)) {
        return false;
      }
      eventKeys.add(key);
      return true;
    }),
  ];
  const filteredEvents = options?.backgroundOnly
    ? mergedEvents.filter(isBackgroundRelevantLocationEvent)
    : mergedEvents;
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
    details?.error instanceof Error
      ? details.error.message
      : details?.error
        ? String(details.error)
        : undefined;
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
      recentFailures: [nextEvent, ...(snapshot.recentFailures ?? [])].slice(
        0,
        MAX_LOCATION_DEBUG_FAILURES,
      ),
    };
  }

  notify();
  persistLocationDebugEventToFile(nextEvent);
  void AsyncStorage.setItem(LOCATION_DEBUG_SNAPSHOT_KEY, JSON.stringify(snapshot)).catch(
    (error) => {
      console.warn('[location-debug] failed to persist snapshot', error);
    },
  );
}
