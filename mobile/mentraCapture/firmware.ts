import { appendMentraDebugLog } from './debug';
import {
  getGlassesMaintenanceStartedAt,
  isGlassesMaintenanceActive,
  loadGlassesMaintenance,
  setGlassesMaintenance,
} from './maintenance';
import {
  getGlassesFirmwareTransport,
  type GlassesFirmwareDevice,
  type GlassesOtaStatus,
} from './sdk';
import { pauseWakeWordListening, resumeWakeWordListening } from './wakeWord';
import { getGlassesAudioRecordingState } from './recordings';
import { getImageEnhancementStatus } from './imageEnhancement';
import { getCaptureSyncStatus } from './sync';

export enum FirmwarePhase {
  Idle = 'idle',
  Checking = 'checking',
  Available = 'available',
  Current = 'current',
  Starting = 'starting',
  Updating = 'updating',
  AwaitingStatus = 'awaiting_status',
  Complete = 'complete',
  Failed = 'failed',
}

export type GlassesFirmwareState = {
  phase: FirmwarePhase;
  progress: number;
  detail: string;
  device: GlassesFirmwareDevice | null;
};

let state: GlassesFirmwareState = {
  phase: FirmwarePhase.Idle,
  progress: 0,
  detail: 'Check for compatible glasses firmware.',
  device: null,
};
const listeners = new Set<(value: GlassesFirmwareState) => void>();
let initialization: Promise<void> | null = null;
let operation: Promise<void> | null = null;
let statusUpdates: Promise<void> = Promise.resolve();
let lastStatusSession: string | null = null;
let previousInstallSession: string | null = null;

function publish(next: Partial<GlassesFirmwareState>): void {
  state = { ...state, ...next };
  listeners.forEach((listener) => listener(state));
}

export function getGlassesFirmwareState(): GlassesFirmwareState {
  return state;
}

export function subscribeGlassesFirmware(
  listener: (value: GlassesFirmwareState) => void,
): () => void {
  listeners.add(listener);
  listener(state);
  return () => {
    listeners.delete(listener);
  };
}

function receiveStatus(status: GlassesOtaStatus, queried = false): Promise<void> {
  statusUpdates = statusUpdates
    .catch(() => undefined)
    .then(async () => {
      void appendMentraDebugLog('glasses_firmware_status', status).catch(() => undefined);
      const session = status.session_id?.trim() || null;
      if (
        session &&
        isGlassesMaintenanceActive() &&
        session === previousInstallSession &&
        (!queried || Date.now() - (getGlassesMaintenanceStartedAt() ?? Date.now()) < 30_000)
      ) {
        // A queued terminal callback from the previous install must not release
        // the new install's capture/microphone barrier.
        return;
      }
      if (session) lastStatusSession = session;
      if (status.status === 'in_progress' || status.status === 'step_complete') {
        if (!isGlassesMaintenanceActive()) {
          await setGlassesMaintenance(true);
          await pauseWakeWordListening('firmware_update');
        }
        publish({
          phase: FirmwarePhase.Updating,
          progress: Number.isFinite(status.overall_percent)
            ? Math.max(0, Math.min(100, status.overall_percent))
            : 0,
          detail:
            status.status === 'step_complete'
              ? 'Finishing this step. The glasses may restart before continuing.'
              : status.phase === 'install'
                ? 'Installing firmware. Keep the glasses powered on.'
                : 'Downloading firmware to the glasses.',
        });
        return;
      }
      if (!['idle', 'complete', 'failed'].includes(status.status)) return;
      // An idle reply immediately after ota_start can predate the install task.
      // Keep ownership until a later query resolves an ambiguous start.
      if (
        status.status === 'idle' &&
        isGlassesMaintenanceActive() &&
        Date.now() - (getGlassesMaintenanceStartedAt() ?? Date.now()) < 30_000
      )
        return;
      if (!isGlassesMaintenanceActive()) return;
      await setGlassesMaintenance(false);
      publish({
        phase:
          status.status === 'complete'
            ? FirmwarePhase.Complete
            : status.status === 'failed'
              ? FirmwarePhase.Failed
              : FirmwarePhase.Idle,
        progress: status.status === 'complete' ? 100 : state.progress,
        detail:
          status.status === 'complete'
            ? 'The glasses reported completion. Check again after they restart to verify their firmware.'
            : status.status === 'failed'
              ? status.error_message || 'The glasses reported an update failure.'
              : 'The glasses report no update in progress. You can check again.',
      });
      void resumeWakeWordListening('firmware_update', 'firmware_finished').catch(() => undefined);
    });
  return statusUpdates;
}

export function initializeGlassesFirmware(): Promise<void> {
  if (!initialization) {
    initialization = (async () => {
      await loadGlassesMaintenance();
      const transport = getGlassesFirmwareTransport();
      transport.subscribe((status) => {
        void receiveStatus(status).catch(reportError);
      });
      if (isGlassesMaintenanceActive()) {
        publish({
          phase: FirmwarePhase.AwaitingStatus,
          detail: 'Waiting to confirm the previous update with the glasses.',
        });
        await pauseWakeWordListening('firmware_update');
      }
      // This process-owned listener survives screen navigation. Never retry ota_start.
      setInterval(() => {
        if (isGlassesMaintenanceActive()) void refreshGlassesFirmware().catch(reportError);
      }, 15_000);
    })().catch((error) => {
      initialization = null;
      throw error;
    });
  }
  return initialization;
}

function reportError(error: unknown): void {
  publish({
    phase: isGlassesMaintenanceActive() ? FirmwarePhase.AwaitingStatus : FirmwarePhase.Failed,
    detail: isGlassesMaintenanceActive()
      ? 'Waiting for the glasses to report update status. Keep them nearby and powered on; installation may still be running.'
      : error instanceof Error
        ? error.message
        : 'Could not check glasses firmware.',
  });
  void appendMentraDebugLog('glasses_firmware_error', { error: String(error) }).catch(
    () => undefined,
  );
}

function runOperation(action: () => Promise<void>): Promise<void> {
  if (operation) return operation;
  operation = action()
    .catch((error) => {
      reportError(error);
      throw error;
    })
    .finally(() => {
      operation = null;
    });
  return operation;
}

export async function refreshGlassesFirmware(): Promise<void> {
  await initializeGlassesFirmware();
  return runOperation(async () => {
    const transport = getGlassesFirmwareTransport();
    let device = await transport.getDevice();
    if (!device.connected && isGlassesMaintenanceActive()) {
      await transport.reconnect();
      device = await transport.getDevice();
    }
    publish({ device });
    if (!device.connected) {
      if (isGlassesMaintenanceActive()) {
        publish({
          phase: FirmwarePhase.AwaitingStatus,
          detail: 'Waiting for the glasses to reconnect after their update.',
        });
        return;
      }
      throw new Error('Connect your Mentra Live in Smart glasses before checking firmware.');
    }
    const wasUpdating = isGlassesMaintenanceActive();
    await receiveStatus(await transport.query(), true);
    // A status poll ends with the update's authoritative outcome. Requesting
    // versions immediately can race its final reboot and replace completion
    // with a transport error. The next explicit check verifies fresh versions.
    if (wasUpdating || isGlassesMaintenanceActive()) return;
    publish({ phase: FirmwarePhase.Checking, detail: 'Checking compatible firmware…' });
    await transport.refreshVersions();
    const available = await transport.check();
    if (isGlassesMaintenanceActive()) return;
    const refreshedDevice = await transport.getDevice();
    if (isGlassesMaintenanceActive()) return;
    publish({
      device: refreshedDevice,
      phase: available ? FirmwarePhase.Available : FirmwarePhase.Current,
      detail: available
        ? 'Compatible firmware is available from Mentra.'
        : 'Your glasses have the firmware required by this app.',
    });
  });
}

/** Called only after the settings screen presents an explicit installation confirmation. */
export async function installGlassesFirmware(): Promise<void> {
  await initializeGlassesFirmware();
  if (operation) throw new Error('Wait for the current firmware check to finish.');
  if (isGlassesMaintenanceActive() || state.phase !== FirmwarePhase.Available) {
    throw new Error('Check for available firmware before starting an update.');
  }
  return runOperation(async () => {
    const transport = getGlassesFirmwareTransport();
    const device = await transport.getDevice();
    publish({ device });
    if (!device.connected || !device.wifiConnected)
      throw new Error('Connect the glasses to Wi-Fi with internet access before installing.');
    if (device.batteryLevel < 50)
      throw new Error('Charge the glasses to at least 50% before installing firmware.');
    if (
      transport.connectionBusy() ||
      getGlassesAudioRecordingState().recording ||
      getImageEnhancementStatus().running ||
      getCaptureSyncStatus().running
    ) {
      throw new Error('Finish the current recording or capture sync before installing firmware.');
    }
    if (await transport.cameraBusy())
      throw new Error('Finish the glasses photo or video recording before installing firmware.');
    // Persist the maintenance barrier before native dispatch, including across app restarts.
    previousInstallSession = lastStatusSession;
    await setGlassesMaintenance(true);
    try {
      await pauseWakeWordListening('firmware_update');
      // A capture or connection operation could have started during the native
      // preflight query. Recheck after closing the maintenance barrier.
      if (
        transport.connectionBusy() ||
        getGlassesAudioRecordingState().recording ||
        getImageEnhancementStatus().running ||
        getCaptureSyncStatus().running
      ) {
        throw new Error('Finish the current recording or capture sync before installing firmware.');
      }
    } catch (error) {
      // No install command was dispatched, so releasing ownership is unambiguous.
      await setGlassesMaintenance(false);
      void resumeWakeWordListening('firmware_update', 'firmware_not_started').catch(
        () => undefined,
      );
      throw error;
    }
    publish({ phase: FirmwarePhase.Starting, progress: 0, detail: 'Starting firmware update…' });
    await transport.start();
    // A start acknowledgement is not completion, and can arrive after progress.
    if (state.phase === FirmwarePhase.Starting) {
      publish({
        phase: FirmwarePhase.Updating,
        detail: 'Update accepted. Waiting for download and installation progress.',
      });
    }
  });
}
