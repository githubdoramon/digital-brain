import { NativeModule, requireOptionalNativeModule } from 'expo';

export type GlassesAlertApp = {
  packageName: string;
  label: string;
};

export type GlassesAlertSettings = {
  enabled: boolean;
  selectedPackages: string[];
  expectedAudioDeviceName: string | null;
};

export type GlassesAlertStatus = {
  notificationAccessGranted: boolean;
  phoneStatePermissionGranted: boolean;
  phoneActivelyInUse: boolean;
  glassesAudioAvailable: boolean;
  glassesAudioDeviceName: string | null;
  settings: GlassesAlertSettings;
};

export type ImageEnhancementDeviceHealth = {
  batteryPercent: number | null;
  charging: boolean | null;
  thermalStatus: number | null;
  thermalStatusLabel: string;
  appMemoryBytes: number;
};

export type ImageEnhancementForegroundServiceStatus = {
  active: boolean;
  startedAtMs: number | null;
  lastNativeTickAtMs: number | null;
  nativeTickCount: number;
};

export type GlassesRuntimeForegroundServiceStatus = {
  active: boolean;
  wakeListeningRequested: boolean;
  automaticCaptureActive: boolean;
  startedAtMs: number | null;
};

export type AppRuntimeStatus = {
  active: boolean;
  owners: string[];
  locationActive: boolean;
  startedAtMs: number | null;
  lastNativeTickAtMs: number | null;
  nativeTickCount: number;
  workRequestCount: number;
  lastWorkDurationMs: number;
  lastError: string | null;
  foregroundTypes: number;
};

export type RuntimeLocationSample = {
  id: string;
  latitude: number;
  longitude: number;
  timestamp: number;
  accuracy: number | null;
  timezone: string;
};

type DigitalBrainGlassesAlertsEvents = {
  onImageEnhancementForegroundTick(event: { timestampMs: number }): void;
  onSpeechPlaybackFinished(event: {
    commandId: string;
    status: 'completed' | 'error' | 'stopped';
    durationMs?: number;
    error?: string;
  }): void;
};

declare class DigitalBrainGlassesAlertsNativeModule extends NativeModule<DigitalBrainGlassesAlertsEvents> {
  setRuntimeLocationEnabled(enabled: boolean): Promise<void>;
  getAppRuntimeStatus(): Promise<AppRuntimeStatus>;
  getRuntimeEnergyDiagnostics(): Promise<Record<string, unknown>>;
  completeRuntimeWork(workToken: string): Promise<void>;
  readRuntimeLocations(): Promise<RuntimeLocationSample[]>;
  acknowledgeRuntimeLocations(ids: string[]): Promise<void>;
  getStatus(): Promise<GlassesAlertStatus>;
  getLaunchableApps(): Promise<GlassesAlertApp[]>;
  saveSettings(enabled: boolean, selectedPackages: string[]): Promise<GlassesAlertSettings>;
  setExpectedGlassesAudioDeviceName(deviceName: string | null): Promise<void>;
  refreshNotificationListener(): Promise<void>;
  openNotificationAccessSettings(): Promise<void>;
  playTestAlert(): Promise<boolean>;
  playTestCallAlert(): Promise<boolean>;
  startImageEnhancementForegroundService(
    intervalMinutes: number,
    scheduleCount?: number,
  ): Promise<void>;
  stopImageEnhancementForegroundService(): Promise<void>;
  getImageEnhancementDeviceHealth(): Promise<ImageEnhancementDeviceHealth>;
  getImageEnhancementForegroundServiceStatus(): Promise<ImageEnhancementForegroundServiceStatus>;
  startGlassesWakeRuntime(): Promise<void>;
  stopGlassesWakeRuntime(): Promise<void>;
  getGlassesRuntimeForegroundServiceStatus(): Promise<GlassesRuntimeForegroundServiceStatus>;
  initializeV8WakeSpotter(): Promise<void>;
  acceptV8WakePcm16(pcmBase64: string): Promise<{ keyword: 'hey_brain' | 'okay_brain'; sampleIndex: number }[]>;
  getV8WakeSpotterStats(): Promise<{
    streamSamples: number;
    acceptedSamplesTotal: number;
    decodeCallsTotal: number;
    keywordResultsTotal: number;
    targetResultsTotal: number;
    rejectResultsTotal: number;
    lastKeywordResult: string;
  } | null>;
  resetV8WakeSpotter(): Promise<void>;
  releaseV8WakeSpotter(): Promise<void>;
  playSpeechAudio(
    commandId: string,
    fileUri: string,
  ): Promise<{ started: boolean; durationMs?: number }>;
  stopSpeechAudio(commandId?: string): Promise<{ stopped: boolean }>;
}

export default requireOptionalNativeModule<DigitalBrainGlassesAlertsNativeModule>(
  'DigitalBrainGlassesAlerts',
);
