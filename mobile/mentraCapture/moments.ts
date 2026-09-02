import AsyncStorage from '@react-native-async-storage/async-storage';

import { apiFetch, getAuthRequestContext } from '@/api/client';
import { getStoredGoogleIdToken, refreshStoredGoogleIdToken } from '@/auth/backgroundToken';
import type { VisualObservation } from '@/image-understanding/types';

import { appendMentraDebugLog } from './debug';
import { resolveCaptureLocation } from './location';
import type { CaptureLocation } from './types';

const QUEUE_KEY = 'digitalbrain.moments.queue.v1';
const SOURCE_TYPE = 'smart_glasses_image';
const LEGACY_OBSERVATION_SCHEMA = 'visual_observation.v2';
const MOMENT_OBSERVATION_SCHEMA = 'moment_observation.v1';
const MAX_BATCH_SIZE = 50;

type MomentQueueEntry = {
  id: string;
  source_type: typeof SOURCE_TYPE;
  observed_at: string;
  observed_timezone: string;
  observed_utc_offset_minutes: number;
  observation: VisualObservation;
  location: CaptureLocation | null;
  attempts: number;
  last_error: string | null;
  created_at: string;
};

type MomentWirePayload = Pick<
  MomentQueueEntry,
  | 'id'
  | 'source_type'
  | 'observed_at'
  | 'observed_timezone'
  | 'observed_utc_offset_minutes'
  | 'observation'
  | 'location'
>;

let queue: MomentQueueEntry[] | null = null;
let drainInFlight: Promise<MomentDrainResult> | null = null;

export type MomentDrainResult = {
  acceptedCount: number;
  rejectedCount: number;
  pendingCount: number;
  attemptedRequest: boolean;
  rejectedDetail?: string;
  deferredReason?: 'no_auth_token' | 'network_error';
};

function createUuid(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function observationForMoment(observation: VisualObservation): VisualObservation {
  const schemaVersion = observation.schema_version as string;
  if (schemaVersion !== MOMENT_OBSERVATION_SCHEMA && schemaVersion !== LEGACY_OBSERVATION_SCHEMA) {
    throw new Error(`Unsupported observation schema: ${schemaVersion}`);
  }
  return { ...observation, schema_version: MOMENT_OBSERVATION_SCHEMA } as VisualObservation;
}

function localTimeContext(observedAt: string): Pick<
  MomentQueueEntry,
  'observed_timezone' | 'observed_utc_offset_minutes'
> {
  const date = new Date(observedAt);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  return {
    observed_timezone: timezone,
    observed_utc_offset_minutes: -date.getTimezoneOffset(),
  };
}

function toMomentWirePayload(entry: MomentQueueEntry): MomentWirePayload {
  // Queue bookkeeping is intentionally local. The Moments API has a strict
  // schema and must receive only the durable, system-meaningful fields.
  return {
    id: entry.id,
    source_type: entry.source_type,
    observed_at: entry.observed_at,
    observed_timezone: entry.observed_timezone,
    observed_utc_offset_minutes: entry.observed_utc_offset_minutes,
    observation: entry.observation,
    location: entry.location,
  };
}

function rejectionDetail(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value).slice(0, 1_000);
  } catch {
    return 'rejected';
  }
}

async function loadQueue(): Promise<MomentQueueEntry[]> {
  if (queue) return queue;
  const raw = await AsyncStorage.getItem(QUEUE_KEY);
  try {
    const parsed = raw ? JSON.parse(raw) : [];
    queue = Array.isArray(parsed) ? parsed : [];
  } catch {
    queue = [];
  }
  return queue;
}

async function persistQueue(): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue ?? []));
}

export async function enqueueImageMoment(
  observedAt: string,
  observation: VisualObservation,
): Promise<void> {
  const entries = await loadQueue();
  const normalized = observationForMoment(observation);
  entries.push({
    id: createUuid(),
    source_type: SOURCE_TYPE,
    observed_at: observedAt,
    ...localTimeContext(observedAt),
    observation: normalized,
    location: null,
    attempts: 0,
    last_error: null,
    created_at: new Date().toISOString(),
  });
  await persistQueue();
  await appendMentraDebugLog('moment_queued', {
    source: SOURCE_TYPE,
    observed_at: observedAt,
    pending_count: entries.length,
  });
}

async function resolveAuth(): Promise<string | null> {
  const context = await getAuthRequestContext();
  return context.token ?? (await getStoredGoogleIdToken());
}

async function enrichLocations(entries: MomentQueueEntry[], token: string): Promise<void> {
  let changed = false;
  for (const entry of entries) {
    if (entry.location) continue;
    entry.location = await resolveCaptureLocation(
      entry.observed_at,
      token,
      refreshStoredGoogleIdToken,
    );
    changed = true;
  }
  if (changed) await persistQueue();
}

export async function drainQueuedMoments(trigger: string): Promise<MomentDrainResult> {
  if (drainInFlight) return drainInFlight;
  const activeDrain = (async (): Promise<MomentDrainResult> => {
    const entries = await loadQueue();
    if (!entries.length) {
      return { acceptedCount: 0, rejectedCount: 0, pendingCount: 0, attemptedRequest: false };
    }
    const token = await resolveAuth();
    if (!token) {
      await appendMentraDebugLog('moment_delivery_deferred', { trigger, reason: 'no_auth_token' });
      return {
        acceptedCount: 0,
        rejectedCount: 0,
        pendingCount: entries.length,
        attemptedRequest: false,
        deferredReason: 'no_auth_token',
      };
    }
    const batch = entries.slice(0, MAX_BATCH_SIZE);
    await enrichLocations(batch, token);
    try {
      const response = (await apiFetch('/mobile/moments/batch', {
        method: 'POST',
        token,
        onAuthExpired: refreshStoredGoogleIdToken,
        body: JSON.stringify({ moments: batch.map(toMomentWirePayload) }),
      })) as { results?: { id?: string; status?: string; detail?: unknown }[] };
      const accepted = new Set(
        (response.results ?? [])
          .filter((result) => ['created', 'updated', 'duplicate'].includes(result.status ?? ''))
          .map((result) => result.id)
          .filter((id): id is string => Boolean(id)),
      );
      const rejected = new Map(
        (response.results ?? [])
          .filter((result) => result.status === 'rejected' && result.id)
          .map((result) => [result.id as string, rejectionDetail(result.detail ?? 'rejected')]),
      );
      queue = entries
        .filter((entry) => !accepted.has(entry.id))
        .map((entry) =>
          rejected.has(entry.id)
            ? { ...entry, attempts: entry.attempts + 1, last_error: rejected.get(entry.id) ?? null }
            : entry,
        );
      await persistQueue();
      await appendMentraDebugLog('moment_delivery_completed', {
        trigger,
        accepted_count: accepted.size,
        rejected_count: rejected.size,
        pending_count: queue.length,
      });
      return {
        acceptedCount: accepted.size,
        rejectedCount: rejected.size,
        pendingCount: queue.length,
        attemptedRequest: true,
        rejectedDetail: rejected.size ? Array.from(rejected.values())[0] : undefined,
      };
    } catch (error) {
      queue = entries.map((entry) => ({
        ...entry,
        attempts: entry.attempts + 1,
        last_error: error instanceof Error ? error.message : 'Moment delivery failed',
      }));
      await persistQueue();
      await appendMentraDebugLog('moment_delivery_failed', {
        trigger,
        pending_count: queue.length,
      });
      return {
        acceptedCount: 0,
        rejectedCount: 0,
        pendingCount: queue.length,
        attemptedRequest: true,
        deferredReason: 'network_error',
      };
    }
  })().finally(() => {
    drainInFlight = null;
  });
  drainInFlight = activeDrain;
  return activeDrain;
}

export async function getQueuedMomentCount(): Promise<number> {
  return (await loadQueue()).length;
}
