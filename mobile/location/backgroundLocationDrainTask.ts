import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';

import { drainQueuedBackgroundLocations } from '@/location/backgroundLocationQueue';
import { BACKGROUND_LOCATION_DRAIN_TASK } from '@/location/backgroundLocationTaskNames';
import { reportLocationDebugEvent } from '@/location/debugState';

const BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES = 15;

function resolveBackgroundTaskStatus(status: number | null): string {
  return typeof status === 'number'
    ? (BackgroundTask.BackgroundTaskStatus[status] ?? String(status))
    : 'unknown';
}

if (!TaskManager.isTaskDefined(BACKGROUND_LOCATION_DRAIN_TASK)) {
  TaskManager.defineTask(BACKGROUND_LOCATION_DRAIN_TASK, async () => {
    try {
      await drainQueuedBackgroundLocations('background_task_worker');
      return BackgroundTask.BackgroundTaskResult.Success;
    } catch (error) {
      reportLocationDebugEvent('background_queue_drain_task_error', {
        error,
      });
      return BackgroundTask.BackgroundTaskResult.Failed;
    }
  });
}

export async function ensureBackgroundLocationDrainTaskRegistered(): Promise<void> {
  const backgroundTaskStatus = await BackgroundTask.getStatusAsync();
  const resolvedBackgroundTaskStatus = resolveBackgroundTaskStatus(backgroundTaskStatus);

  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_LOCATION_DRAIN_TASK);
  if (isRegistered) {
    reportLocationDebugEvent('background_drain_task_already_registered', {
      payload: {
        background_task_status: resolvedBackgroundTaskStatus,
      },
      recordInHistory: false,
    });
    return;
  }

  await BackgroundTask.registerTaskAsync(BACKGROUND_LOCATION_DRAIN_TASK, {
    minimumInterval: BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES,
  });
  reportLocationDebugEvent('background_drain_task_registered', {
    payload: {
      minimum_interval_minutes: BACKGROUND_DRAIN_MIN_INTERVAL_MINUTES,
      background_task_status: resolvedBackgroundTaskStatus,
    },
    recordInHistory: false,
  });
}

export async function unregisterBackgroundLocationDrainTask(): Promise<void> {
  await BackgroundTask.unregisterTaskAsync(BACKGROUND_LOCATION_DRAIN_TASK).catch(() => undefined);
}

export async function getBackgroundLocationDrainWorkerStatus(): Promise<string> {
  return resolveBackgroundTaskStatus(await BackgroundTask.getStatusAsync());
}

export { resolveBackgroundTaskStatus };
