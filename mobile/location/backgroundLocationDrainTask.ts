import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import { drainQueuedBackgroundLocations } from '@/location/backgroundLocationQueue';
import { BACKGROUND_LOCATION_DRAIN_TASK } from '@/location/backgroundLocationTaskNames';

import { isLocationTrackingEnabled } from '@/location/trackingPreference';

const BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES = 15;

function resolveBackgroundTaskStatus(status: number | null): string {
  return typeof status === 'number'
    ? (BackgroundTask.BackgroundTaskStatus[status] ?? String(status))
    : 'unknown';
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_DRAIN_TASK, async () => {
    // Android uploads are owned by WorkManager. Keep this task for iOS and
    // migration cleanup, while ignoring any stale Android registration.
    if (Platform.OS === 'android') return BackgroundTask.BackgroundTaskResult.Success;
    try {
      if (!(await isLocationTrackingEnabled())) return BackgroundTask.BackgroundTaskResult.Success;
      await drainQueuedBackgroundLocations('background_task_worker');
      return BackgroundTask.BackgroundTaskResult.Success;
    } catch {
      return BackgroundTask.BackgroundTaskResult.Failed;
    }
  });
}

export async function ensureBackgroundLocationDrainTaskRegistered(): Promise<void> {
  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_LOCATION_DRAIN_TASK);
  if (isRegistered) {
    return;
  }

  await BackgroundTask.registerTaskAsync(BACKGROUND_LOCATION_DRAIN_TASK, {
    minimumInterval: BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES,
  });
}

export async function unregisterBackgroundLocationDrainTask(): Promise<void> {
  await BackgroundTask.unregisterTaskAsync(BACKGROUND_LOCATION_DRAIN_TASK).catch(() => undefined);
}

export async function getBackgroundLocationDrainWorkerStatus(): Promise<string> {
  return resolveBackgroundTaskStatus(await BackgroundTask.getStatusAsync());
}

export { resolveBackgroundTaskStatus };
