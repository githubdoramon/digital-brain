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

export type GlassesStatus = {
  media: { pending: number; status: string };
  enabled: boolean;
  signedIn: boolean;
  running: boolean;
  savedName: string | null;
  connection: string;
  ready: boolean;
  bluetoothOn: boolean;
  permitted: boolean;
  battery: number | null;
  firmwareVersion: string | null;
  firmwareSource: string | null;
  appVersion: string | null;
  scanning: boolean;
  devices: { id: string; name: string }[];
  retryAttempt: number;
  nextRetryAtMs: number | null;
  updateAvailable: boolean | null;
  updateActive: boolean;
  updateProgress: number;
  updateStatus: string;
  firmwareBusy: boolean;
  error: string | null;
  firmwareError: string | null;
};

export type GlassesWifiNetwork = {
  ssid: string;
  requiresPassword: boolean;
  signalStrength: number;
};

export type GlassesAlertsStatus = {
  notifications: boolean;
  calls: boolean;
  packages: string[];
  chimeVolume: number;
  callVolume: number;
  notificationAccess: boolean;
  listenerConnected: boolean;
  phonePermission: boolean;
  glassesReady: boolean;
  audioReady: boolean;
  phoneInUse: boolean;
  mediaMuted: boolean;
  dndActive: boolean;
  dndBlocksMedia: boolean;
  playing: 'call' | 'chime' | 'preview' | null;
  lastPlaybackError: string | null;
};

export type GlassesAlertApp = { packageName: string; label: string };

// Native methods are supplied by Expo at runtime; this contract emits no class fields.
type DigitalBrainRuntimeModule = NativeModule & {
  setGlassesSignedIn(signedIn: boolean): Promise<void>;
  setGlassesEnabled(enabled: boolean): Promise<void>;
  syncGlassesMedia(): Promise<void>;
  getGlassesStatus(): Promise<GlassesStatus>;
  scanGlasses(): Promise<void>;
  selectGlasses(id: string): Promise<void>;
  connectGlasses(): Promise<void>;
  forgetGlasses(): Promise<void>;
  checkGlassesFirmware(): Promise<void>;
  updateGlassesFirmware(): Promise<void>;
  scanGlassesWifi(): Promise<GlassesWifiNetwork[]>;
  configureGlassesWifi(ssid: string, password: string): Promise<void>;
  getGlassesDiagnostics(): Promise<Record<string, unknown>>;
  getGlassesAlertsStatus(): Promise<GlassesAlertsStatus>;
  getGlassesAlertApps(): Promise<GlassesAlertApp[]>;
  saveGlassesAlerts(
    notifications: boolean,
    calls: boolean,
    packages: string[],
    chime: number,
    call: number,
  ): Promise<void>;
  openGlassesNotificationAccess(): Promise<void>;
  openGlassesSoundSettings(): Promise<void>;
  testGlassesAlert(call: boolean): Promise<void>;
  stopGlassesAlertTest(): Promise<void>;
  refreshGlassesAlerts(): Promise<void>;
  setRuntimeLocationEnabled(enabled: boolean): Promise<void>;
  configureRuntimeLocationUploader(apiBaseUrl: string, googleWebClientId: string): Promise<void>;
  getAppRuntimeStatus(): Promise<AppRuntimeStatus>;
  getRuntimeEnergyDiagnostics(): Promise<Record<string, unknown>>;
};

export default requireOptionalNativeModule<DigitalBrainRuntimeModule>('DigitalBrainRuntime');
