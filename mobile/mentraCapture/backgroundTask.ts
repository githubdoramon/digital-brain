import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';

import { getCaptureSyncStatus, reconcileGlassesCaptures } from './sync';

export const GLASSES_CAPTURE_RECONCILIATION_TASK = 'digitalbrain-glasses-capture-reconciliation';

if (!TaskManager.isTaskDefined(GLASSES_CAPTURE_RECONCILIATION_TASK)) {
  TaskManager.defineTask(GLASSES_CAPTURE_RECONCILIATION_TASK, async () => {
    try {
      await reconcileGlassesCaptures();
      return getCaptureSyncStatus().lastError
        ? BackgroundTask.BackgroundTaskResult.Failed
        : BackgroundTask.BackgroundTaskResult.Success;
    } catch {
      return BackgroundTask.BackgroundTaskResult.Failed;
    }
  });
}

export async function registerGlassesCaptureReconciliation(): Promise<void> {
  if (!(await TaskManager.isTaskRegisteredAsync(GLASSES_CAPTURE_RECONCILIATION_TASK))) {
    await BackgroundTask.registerTaskAsync(GLASSES_CAPTURE_RECONCILIATION_TASK, {
      // Expo expresses this interval in minutes. The OS may defer execution,
      // but it will not schedule this worker more frequently than fifteen minutes.
      minimumInterval: 15,
    });
  }
}

export async function unregisterGlassesCaptureReconciliation(): Promise<void> {
  await BackgroundTask.unregisterTaskAsync(GLASSES_CAPTURE_RECONCILIATION_TASK).catch(
    () => undefined,
  );
}
