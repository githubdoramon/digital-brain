import { AppRegistry } from 'react-native';

import RuntimeNative from '@/modules/digital-brain-runtime/src';
import { transferNativeLocations } from '@/location/foregroundLocation';
import { drainQueuedBackgroundLocations } from '@/location/backgroundLocationQueue';
import { reportLocationDebugEvent } from '@/location/debugState';

async function performForegroundRuntimeWork(): Promise<void> {
  if (!RuntimeNative) return;
  try {
    const status = await RuntimeNative.getAppRuntimeStatus();
    reportLocationDebugEvent('foreground_runtime_work', {
      payload: { runtime: status },
      recordInHistory: false,
    });
    await transferNativeLocations();
    if ((await RuntimeNative.getAppRuntimeStatus()).owners.includes('location')) {
      await drainQueuedBackgroundLocations('foreground_service');
    }
  } catch (error) {
    reportLocationDebugEvent('foreground_runtime_location_work_error', { error });
  }
}

export async function runForegroundRuntimeWork(input?: {
  reason?: string;
  workToken?: string;
}): Promise<void> {
  const startedAt = Date.now();
  try {
    await performForegroundRuntimeWork();
  } finally {
    try {
      const energy = await RuntimeNative?.getRuntimeEnergyDiagnostics();
      reportLocationDebugEvent('foreground_runtime_energy_sample', { payload: energy });
    } catch (error) {
      // Unsupported counters/older native builds cannot prevent worker completion.
      reportLocationDebugEvent('foreground_runtime_energy_error', { error });
    }
    try {
      reportLocationDebugEvent('foreground_runtime_work_finished', {
        payload: {
          reason: input?.reason ?? 'unknown',
          durationMs: Date.now() - startedAt,
        },
      });
    } finally {
      if (input?.workToken) {
        try {
          await RuntimeNative?.completeRuntimeWork?.(input.workToken);
        } catch (error) {
          // The bounded native timeout remains the safety net on bridge failure.
          reportLocationDebugEvent('foreground_runtime_completion_error', { error });
        }
      }
    }
  }
}

// Evaluated before Expo Router, including native/headless process recreation.
AppRegistry.registerHeadlessTask('DigitalBrainRuntimeWork', () => runForegroundRuntimeWork);
