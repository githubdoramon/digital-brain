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
  media: { pending: number; status: string; transport: string; networkNote: string | null };
  wifi: { connected: boolean; ssid: string | null; address: string | null };
  hotspotEnabled: boolean;
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
  playing: 'call' | 'chime' | 'preview' | 'wake' | 'command' | null;
  lastPlaybackError: string | null;
};

export type GlassesAlertApp = { packageName: string; label: string };

// Native methods are supplied by Expo at runtime; this contract emits no class fields.
type DigitalBrainRuntimeModule = NativeModule & {
  setGlassesSignedIn(signedIn: boolean): Promise<void>;
  setGlassesEnabled(enabled: boolean): Promise<void>;
  getGlassesSpeechTestStatus(): Promise<SpeechTestStatus>;
  startGlassesSpeechTest(folder: string, models: string[], repeats: number): Promise<void>;
  resumeGlassesSpeechTest(): Promise<void>;
  pauseGlassesSpeechTest(): Promise<void>;
  exportGlassesSpeechTest(baseUri: string): Promise<void>;
  getGlassesListeningStatus(): Promise<GlassesListeningStatus>;
  setGlassesListeningMinimumVolume(value: number): Promise<void>;
  setGlassesListening(enabled: boolean, baseUri: string | null): Promise<void>;
  retryGlassesWakeSaves(baseUri: string): Promise<void>;
  testGlassesLight(
    color: 'red' | 'green' | 'blue' | 'orange' | 'white',
    pattern: 'quick' | 'double' | 'triple' | 'slow' | 'hold',
  ): Promise<{ attempt: number; sent: boolean }>;
  endGlassesLightTest(): Promise<void>;
  reportGlassesLightTest(attempt: number, visible: boolean): Promise<void>;
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
  getGlassesRecordingStatus(): Promise<GlassesRecordingStatus>;
  startGlassesRecording(baseUri: string): Promise<void>;
  stopGlassesRecording(): Promise<void>;
  retryGlassesRecordingSave(baseUri: string, id: string | null): Promise<void>;
  playGlassesRecording(uri: string): Promise<void>;
  seekGlassesRecording(ms: number): Promise<void>;
  stopGlassesRecordingPlayback(): Promise<void>;
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
  testGlassesReply(text: string): Promise<{ status: string; audioError: string | null }>;
  cancelGlassesReplyTest(): Promise<void>;
  stopGlassesAlertTest(): Promise<void>;
  refreshGlassesAlerts(): Promise<void>;
  setRuntimeLocationEnabled(enabled: boolean): Promise<void>;
  configureRuntimeLocationUploader(apiBaseUrl: string, googleWebClientId: string): Promise<void>;
  getAppRuntimeStatus(): Promise<AppRuntimeStatus>;
  getRuntimeEnergyDiagnostics(): Promise<Record<string, unknown>>;
};

export default requireOptionalNativeModule<DigitalBrainRuntimeModule>('DigitalBrainRuntime');

export type GlassesRecordingSave = {
  id: string;
  name: string;
  startedAtMs: number;
  durationMs: number;
  state: 'FINISHING' | 'QUEUED' | 'ENCODING' | 'COPYING' | 'VERIFYING' | 'FAILED';
  progress: number | null;
  targetUri: string | null;
};

export type GlassesRecordingStatus = {
  state: 'IDLE' | 'STARTING' | 'RECORDING';
  canStart: boolean;
  libraryRevision: number;
  processing: GlassesRecordingSave[];
  startedAtMs: number;
  durationMs: number;
  message: string | null;
  savedUri: string | null;
  ready: boolean;
  playback: {
    uri: string | null;
    playing: boolean;
    durationMs: number;
    positionMs: number;
    error: string | null;
  };
};

export type GlassesListeningStatus = {
  capturing: boolean;
  savePending: number;
  minimumVolume: number;
  audioLevel: number;
  enabled: boolean;
  active: boolean;
  status: string;
  count: number;
  pending: number;
  lastTranscriptAtMs: number | null;
};

export type SpeechTestSummary = {
  candidate: string;
  mode: 'cold' | 'warm' | 'first' | 'repeat';
  count: number;
  completed: number;
  failures: number;
  correct: number;
  positives: number;
  negatives: number;
  missed: number;
  wrong: number;
  falseAccepts: number;
  failureReasons?: Record<string, number>;
  accuracy: number | null;
  wer: number | null;
  medianMs: number | null;
  p95Ms: number | null;
};
export type SpeechTestStatus = {
  state: 'empty' | 'ready' | 'running' | 'paused' | 'complete';
  completed: number;
  total: number;
  samples?: number;
  message?: string;
  summary: SpeechTestSummary[];
};
