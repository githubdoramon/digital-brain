import { apiFetch } from '@/api/client';

import {
  CAPTURE_LOCATION_TOLERANCE_MS,
  findNearestQueuedLocation,
  type HistoricalLocationSample,
} from '@/location/backgroundLocationQueue';

import type { CaptureLocation } from './types';

type AuthExpiredHandler = () => Promise<string | null>;

function withProvenance(
  sample: HistoricalLocationSample & { offsetMs: number },
  captureAt: string,
): CaptureLocation {
  return {
    lat: sample.lat,
    lon: sample.lon,
    ...(sample.accuracy_m == null ? {} : { accuracy_m: sample.accuracy_m }),
    captured_at: captureAt,
    source: 'phone_location_history',
    provenance: 'phone_location_history',
    sample_captured_at: sample.captured_at,
    sample_source: sample.source,
    offset_ms: sample.offsetMs,
    tolerance_ms: CAPTURE_LOCATION_TOLERANCE_MS,
  };
}

function normalizeBackendSample(
  payload: unknown,
  captureAt: string,
): (HistoricalLocationSample & { offsetMs: number }) | null {
  const data = (payload as { location?: unknown } | null)?.location ?? payload;
  if (!data || typeof data !== 'object') return null;
  const candidate = data as Record<string, unknown>;
  const lat = Number(candidate.lat);
  const lon = Number(candidate.lon);
  const sampleCapturedAt = String(candidate.sample_captured_at ?? candidate.captured_at ?? '');
  const offsetMs = Number(candidate.offset_ms);
  if (
    !Number.isFinite(lat) ||
    !Number.isFinite(lon) ||
    !sampleCapturedAt ||
    !Number.isFinite(Date.parse(sampleCapturedAt))
  ) {
    return null;
  }
  return {
    lat,
    lon,
    ...(Number.isFinite(Number(candidate.accuracy_m))
      ? { accuracy_m: Number(candidate.accuracy_m) }
      : {}),
    captured_at: sampleCapturedAt,
    source: String(candidate.sample_source ?? candidate.source ?? 'unknown'),
    offsetMs: Number.isFinite(offsetMs)
      ? Math.abs(offsetMs)
      : Math.abs(Date.parse(sampleCapturedAt) - Date.parse(captureAt)),
  };
}

/**
 * Resolve the nearest phone location at capture time. Local queued samples
 * are preferred because they may not have reached the backend yet; the
 * backend history is the fallback after a previous location drain.
 */
export async function resolveCaptureLocation(
  capturedAt: string | null,
  token: string | null,
  onAuthExpired: AuthExpiredHandler,
): Promise<CaptureLocation | null> {
  if (!capturedAt || !Number.isFinite(Date.parse(capturedAt))) return null;

  const local = await findNearestQueuedLocation(capturedAt);
  if (local) return withProvenance(local, capturedAt);
  if (!token) return null;

  try {
    const query = encodeURIComponent(capturedAt);
    const payload = await apiFetch(`/mobile/location/history/nearest?captured_at=${query}`, {
      method: 'GET',
      token,
      onAuthExpired,
    });
    const backendSample = normalizeBackendSample(payload, capturedAt);
    return backendSample ? withProvenance(backendSample, capturedAt) : null;
  } catch {
    // Location is useful metadata, but must never prevent durable media upload.
    return null;
  }
}
