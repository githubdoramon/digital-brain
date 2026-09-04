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

type DigitalBrainGlassesAlertsEvents = {
  onImageEnhancementForegroundTick(event: { timestampMs: number }): void;
};

declare class DigitalBrainGlassesAlertsNativeModule extends NativeModule<DigitalBrainGlassesAlertsEvents> {
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
}

export default requireOptionalNativeModule<DigitalBrainGlassesAlertsNativeModule>(
  'DigitalBrainGlassesAlerts',
);
