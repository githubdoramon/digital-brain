import { Platform } from 'react-native';

import RuntimeNative from '@/modules/digital-brain-glasses-alerts/src';
import { enqueueBackgroundLocationEntry } from './backgroundLocationQueue';
import { reportLocationDebugEvent } from './debugState';
import { getLocationRuntimeState } from './runtimeState';

export function hasSharedLocationRuntime(): boolean {
  return Platform.OS === 'android' && typeof RuntimeNative?.readRuntimeLocations === 'function';
}

let transferInFlight: Promise<void> | null = null;

/** Capture handoff only. Never read auth or upload until samples have a durable JS copy. */
export async function transferNativeLocations(): Promise<void> {
  if (!hasSharedLocationRuntime() || !RuntimeNative) return;
  if (transferInFlight) return transferInFlight;
  transferInFlight = (async () => {
    const samples = await RuntimeNative.readRuntimeLocations();
    const acknowledged: string[] = [];
    for (const sample of samples) {
      if (
        !Number.isFinite(sample.latitude) ||
        Math.abs(sample.latitude) > 90 ||
        !Number.isFinite(sample.longitude) ||
        Math.abs(sample.longitude) > 180 ||
        !Number.isFinite(sample.timestamp) ||
        sample.timestamp <= 0
      )
        throw new Error('Invalid native location sample');
      const capturedAt = new Date(sample.timestamp).toISOString();
      const batchId = `native:${sample.id}`;
      await enqueueBackgroundLocationEntry({
        id: `${sample.timestamp}:${sample.latitude.toFixed(6)}:${sample.longitude.toFixed(6)}`,
        lat: sample.latitude,
        lon: sample.longitude,
        accuracyM: sample.accuracy ?? undefined,
        capturedAt,
        capturedAtMs: sample.timestamp,
        source: 'android_foreground_location',
        timezone: sample.timezone,
        debugRequestId: batchId,
        batchId,
        sampleIndex: 1,
        sampleCount: 1,
        batchFirstCapturedAt: capturedAt,
        batchLastCapturedAt: capturedAt,
        executionContext: getLocationRuntimeState().appState,
        sampleAgeSeconds: Math.max(0, Math.round((Date.now() - sample.timestamp) / 1000)),
        isBufferedFlush: false,
        enqueuedAt: new Date().toISOString(),
        attemptCount: 0,
      });
      acknowledged.push(sample.id);
    }
    if (acknowledged.length) await RuntimeNative.acknowledgeRuntimeLocations(acknowledged);
    reportLocationDebugEvent('foreground_location_handoff', {
      payload: { transferred_count: acknowledged.length },
      recordInHistory: false,
    });
  })();
  try {
    await transferInFlight;
  } finally {
    transferInFlight = null;
  }
}
