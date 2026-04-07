export type LocationDebugSnapshot = {
  lastEventAt?: string;
  lastEventName?: string;
  lastMessage?: string;
  lastError?: string;
  lastPayload?: Record<string, unknown>;
};

type Listener = (snapshot: LocationDebugSnapshot) => void;

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
  console.info('[location-debug]', eventName, {
    message: details?.message,
    payload: details?.payload,
    error: details?.error instanceof Error ? details.error.message : details?.error,
  });
  snapshot = {
    ...snapshot,
    lastEventAt: new Date().toISOString(),
    lastEventName: eventName,
    lastMessage: details?.message,
    lastPayload: details?.payload,
    lastError: details?.error instanceof Error ? details.error.message : details?.error ? String(details.error) : undefined,
  };
  notify();
}
