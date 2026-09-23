import { AppRegistry, PermissionsAndroid, Platform } from 'react-native';

import RuntimeNative from '@/modules/digital-brain-glasses-alerts/src';
import { transferNativeLocations } from '@/location/foregroundLocation';
import { drainQueuedBackgroundLocations } from '@/location/backgroundLocationQueue';
import { reportLocationDebugEvent } from '@/location/debugState';
import { ensureMentraConnection } from '@/mentraCapture/sdk';
import { assertGlassesNotUpdating } from '@/mentraCapture/maintenance';

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

  // Location storage/network failures must not block the independent glasses
  // owner. Uploads can take seconds; never reconnect using their old snapshot.
  try {
    if ((await RuntimeNative.getAppRuntimeStatus()).owners.includes('glasses')) {
      // Reattach a desired session after native process recreation. Never open
      // a Bluetooth permission dialog from headless work or interrupt OTA.
      const permitted =
        Platform.OS === 'android' &&
        (Number(Platform.Version) < 31 ||
          ((await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT)) &&
            (await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN))));
      if (permitted) {
        await assertGlassesNotUpdating();
        if ((await RuntimeNative.getAppRuntimeStatus()).owners.includes('glasses')) {
          await ensureMentraConnection({ applyCaptureDefaults: false });
        }
      }
    }
  } catch (error) {
    reportLocationDebugEvent('foreground_runtime_glasses_work_error', { error });
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
      let deviceHealth = null;
      try {
        if (typeof RuntimeNative?.getImageEnhancementDeviceHealth === 'function') {
          deviceHealth = await RuntimeNative.getImageEnhancementDeviceHealth();
        }
      } catch {
        // Health sampling is diagnostic and must never fail the runtime task.
      }
      try {
        const energy = await RuntimeNative?.getRuntimeEnergyDiagnostics?.();
        reportLocationDebugEvent('foreground_runtime_energy_sample', { payload: energy });
      } catch (error) {
        // Unsupported counters/older native builds cannot prevent worker completion.
        reportLocationDebugEvent('foreground_runtime_energy_error', { error });
      }
      reportLocationDebugEvent('foreground_runtime_work_finished', {
        payload: {
          reason: input?.reason ?? 'unknown',
          durationMs: Date.now() - startedAt,
          deviceHealth,
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
