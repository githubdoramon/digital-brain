import { requireOptionalNativeModule } from 'expo';

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

type DigitalBrainGlassesAlertsNativeModule = {
  getStatus(): Promise<GlassesAlertStatus>;
  getLaunchableApps(): Promise<GlassesAlertApp[]>;
  saveSettings(enabled: boolean, selectedPackages: string[]): Promise<GlassesAlertSettings>;
  setExpectedGlassesAudioDeviceName(deviceName: string | null): Promise<void>;
  refreshNotificationListener(): Promise<void>;
  openNotificationAccessSettings(): Promise<void>;
  playTestAlert(): Promise<boolean>;
  playTestCallAlert(): Promise<boolean>;
};

export default requireOptionalNativeModule<DigitalBrainGlassesAlertsNativeModule>(
  'DigitalBrainGlassesAlerts',
);
