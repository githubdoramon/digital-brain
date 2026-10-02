import { NativeModule, requireOptionalNativeModule } from 'expo';

export type AppRuntimeStatus = {
  active: boolean;
  owners: string[];
  locationActive: boolean;
  startedAtMs: number | null;
  nativeUploadRunCount: number;
  nativeUploadSampleCount: number;
  nativeUploadLastRunAtMs?: number | null;
  nativeUploadLastDurationMs: number;
  nativeUploadLastOutcome: string;
  nativeUploadLastHttpStatus: number | null;
  nativeUploadLastQueueSize: number;
  nativeUploadRecentRuns?: NativeUploadRun[];
  nativeLocationQueueSize: number;
  nativeLocationQueueSnapshot?: NativeLocationQueueSnapshot;
  nativeLocationRecentEvents?: NativeLocationDiagnosticEvent[];
  nativeLocationDiagnosticsInfo?: {
    retainedCount: number;
    retainedLimit: number;
    droppedTotal: number;
    firstSequence: number | null;
    lastSequence: number | null;
  };
  nativeLocationWorkManager?: NativeLocationWorkManagerSnapshot;
  nativeLocationUploadConfig?: {
    available: boolean;
    endpoint: string | null;
  };
  lastError: string | null;
  foregroundTypes: number;
};

export type NativeUploadRun = {
  atMs: number;
  durationMs: number;
  uploaded: number;
  queued: number;
  outcome: string;
  httpStatus: number | null;
  processCpuMs: number;
  deviceAwakeMs: number;
  batteryChargeDeltaMicroAh: number;
};

export type NativeLocationDiagnosticEvent = {
  atMs: number;
  event: string;
  [key: string]: unknown;
};

export type NativeLocationQueueSnapshot = {
  available: boolean;
  count?: number;
  oldestCapturedAt?: string | null;
  newestCapturedAt?: string | null;
  sampleKeys?: string[];
  errorType?: string;
};

export type NativeLocationWorkManagerSnapshot = {
  available: boolean;
  workCount?: number;
  stateCounts?: Record<string, number>;
  items?: {
    id: string;
    state: string;
    runAttemptCount: number;
    tags: string[];
  }[];
  truncated?: boolean;
  errorType?: string;
};

class DigitalBrainRuntimeModule extends NativeModule {
  setRuntimeLocationEnabled(_enabled: boolean): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  configureRuntimeLocationUploader(_apiBaseUrl: string, _googleWebClientId: string): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  getAppRuntimeStatus(): Promise<AppRuntimeStatus> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  getRuntimeEnergyDiagnostics(): Promise<Record<string, unknown>> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
}

export default requireOptionalNativeModule<DigitalBrainRuntimeModule>('DigitalBrainRuntime');
