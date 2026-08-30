import type { CaptureKind } from './types';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Buffer } from 'buffer';
import * as FileSystem from 'expo-file-system/legacy';
import { PermissionsAndroid, Platform } from 'react-native';

import { appendMentraDebugLog } from './debug';
import { setExpectedGlassesAlertAudioDevice } from '@/glassesAlerts/runtime';

type Subscription = { remove: () => void };
export type MentraDevice = {
  id: string;
  model: string;
  name: string;
  address?: string;
  rssi?: number;
};
type ScanOptions = {
  timeoutMs?: number;
  onResults?: (devices: MentraDevice[]) => void;
};
type BluetoothSdk = {
  addListener: (event: string, listener: (event: any) => void) => Subscription;
  getDefaultDevice: () => Promise<MentraDevice | null>;
  setDefaultDevice: (device: MentraDevice | null) => Promise<void>;
  clearDefaultDevice: () => Promise<void>;
  scan: (model: string, options?: ScanOptions) => Promise<MentraDevice[]>;
  stopScan: () => Promise<void>;
  connect: (
    device: MentraDevice,
    options?: { saveAsDefault?: boolean; cancelExistingConnectionAttempt?: boolean },
  ) => Promise<void>;
  connectDefault: (options?: { cancelExistingConnectionAttempt?: boolean }) => Promise<void>;
  forget: () => Promise<void>;
  setGalleryModeEnabled: (enabled: boolean) => Promise<unknown>;
  setPhotoCaptureDefaults: (settings: Record<string, unknown>) => Promise<unknown>;
  setVideoRecordingDefaults: (settings: {
    width: number;
    height: number;
    fps: number;
  }) => Promise<unknown>;
  setMaxVideoRecordingDuration: (minutes: number) => Promise<unknown>;
  setHotspotState: (enabled: boolean) => Promise<unknown>;
  setMicState: (
    enabled: boolean,
    useGlassesMic?: boolean,
    sendTranscript?: boolean,
    sendLc3Data?: boolean,
  ) => Promise<void>;
  startGlassesM4aRecording: (outputUri: string) => Promise<GlassesM4aRecordingResult>;
  stopGlassesM4aRecording: (reason: string) => Promise<GlassesM4aRecordingResult>;
  recoverGlassesM4aRecording: () => Promise<GlassesM4aRecoveryResult>;
  getGlassesM4aRecordingStatus: () => Promise<GlassesM4aRecordingStatus>;
  playGlassesM4aRecording: (
    outputUri: string,
  ) => Promise<{ playing: boolean; durationMs?: number }>;
  stopGlassesM4aPlayback: () => Promise<{ playing: boolean }>;
};

export type GlassesM4aRecordingResult = {
  completed: boolean;
  reason: string;
  outputUri: string;
  durationMs?: number;
  sizeBytes?: number;
  startedAt?: number;
};

export type GlassesM4aRecoveryResult = {
  recovered: boolean;
  outputUri: string | null;
};

export type GlassesM4aRecordingStatus = {
  recording: boolean;
  outputUri: string | null;
  startedAt: number | null;
};
type InternalBluetoothSdk = BluetoothSdk & {
  getGlassesStatus?: () => Promise<{
    connection?: { state?: string; fullyBooted?: boolean };
    deviceModel?: string;
    galleryModeEnabled?: boolean;
  }>;
  onGlassesStatus?: (
    listener: (status: { connection?: { state?: string; fullyBooted?: boolean } }) => void,
  ) => () => void;
};

const DEFAULT_DEVICE_STORAGE_KEY = 'digitalbrain.mentra.default.device.v1';

type LocalNetworkModule = {
  connect?: (ssid: string, password: string) => Promise<unknown>;
  request?: (
    requestId: string,
    url: string,
    method: string,
    headers: Record<string, string>,
    body: string | null,
    timeoutMs: number,
  ) => Promise<{ status: number; headers: Record<string, string>; bodyBase64: string }>;
  download?: (
    requestId: string,
    url: string,
    destination: string,
    headers: Record<string, string>,
    connectionTimeoutMs: number,
    readTimeoutMs: number,
  ) => Promise<{ statusCode: number; bytesWritten: number; headers: Record<string, string> }>;
  disconnect?: () => Promise<void>;
};

let sdk: BluetoothSdk | null | undefined;
let internalSdk: InternalBluetoothSdk | null | undefined;
let wifiIp: string | null = null;
let hotspot: { localIp: string; ssid: string; password: string } | null = null;
let localNetwork: LocalNetworkModule | null = null;
let scopedNetworkActive = false;
let stateListenersInitialized = false;
let diagnosticsListenersInitialized = false;
let lastNativeLogAt = 0;

function debugSdk(event: string, payload?: unknown): void {
  void appendMentraDebugLog(event, payload).catch(() => undefined);
}

const DIAGNOSTIC_SDK_EVENTS = [
  'log',
  'device_discovered',
  'default_device_changed',
  'glasses_not_ready',
  'button_press',
  'wifi_status_change',
  'hotspot_status_change',
  'hotspot_error',
  'photo_response',
  'photo_status',
  'camera_status',
  'video_recording_status',
  'media_success',
  'media_error',
  'gallery_status',
  'settings_ack',
  'pair_failure',
  'audio_pairing_needed',
  'audio_connected',
  'audio_disconnected',
  'rgb_led_control_response',
  'version_info',
] as const;

function initializeDiagnosticsListeners(native: BluetoothSdk): void {
  if (diagnosticsListenersInitialized) return;
  diagnosticsListenersInitialized = true;
  DIAGNOSTIC_SDK_EVENTS.forEach((eventName) => {
    try {
      native.addListener(eventName, (payload) => {
        if (eventName === 'log') {
          const now = Date.now();
          if (now - lastNativeLogAt < 100) return;
          lastNativeLogAt = now;
        }
        // Keep the event type visible in diagnostics. The payload redactor intentionally
        // removes generic keys such as `name`/`id`, so using a dedicated field here avoids
        // turning the most useful part of the log into "[redacted]".
        debugSdk('sdk_event', { sdkEvent: eventName, payload });
      });
    } catch (error) {
      debugSdk('sdk_listener_error', { sdkEvent: eventName, error: String(error) });
    }
  });
  debugSdk('sdk_loaded', { diagnosticEvents: DIAGNOSTIC_SDK_EVENTS });
}

function updateWifiState(event: any): void {
  wifiIp = event?.state === 'connected' && typeof event.localIp === 'string' ? event.localIp : null;
}

function updateHotspotState(event: any): void {
  hotspot =
    event?.state === 'enabled' &&
    typeof event.localIp === 'string' &&
    typeof event.ssid === 'string' &&
    typeof event.password === 'string'
      ? event
      : null;
}

function loadSdk(): BluetoothSdk | null {
  if (sdk !== undefined) return sdk;
  try {
    // The native SDK is optional in JS-only/web builds. Native Android builds install it.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const module = require('@mentra/bluetooth-sdk');
    sdk = (module.BluetoothSdk ?? module.default) as BluetoothSdk;
    try {
      // The published SDK keeps the Android scoped-network bridge on its internal export.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      localNetwork = require('@mentra/bluetooth-sdk/internal').MentraLocalNetwork ?? null;
    } catch {
      localNetwork = null;
    }
    // Keep transport state available to headless background-task launches,
    // where the navigation tree (and its UI subscription) is not mounted.
    const native = sdk;
    if (native && !stateListenersInitialized) {
      native.addListener('wifi_status_change', updateWifiState);
      native.addListener('hotspot_status_change', updateHotspotState);
      stateListenersInitialized = true;
    }
    if (native) initializeDiagnosticsListeners(native);
  } catch {
    sdk = null;
    debugSdk('sdk_load_error', { error: 'Bluetooth SDK module unavailable' });
  }
  return sdk;
}

function loadInternalSdk(): InternalBluetoothSdk | null {
  if (internalSdk !== undefined) return internalSdk;
  try {
    // The internal facade exposes status snapshots/listeners that are intentionally not part of
    // the public command surface. It is used only to wait for a selected device to finish booting.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const module = require('@mentra/bluetooth-sdk/internal');
    internalSdk = (module.default ?? module) as InternalBluetoothSdk;
  } catch {
    internalSdk = null;
  }
  return internalSdk;
}

export function isMentraSdkAvailable(): boolean {
  return Boolean(loadSdk());
}

function isGlassesAudioRecorderUnavailable(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.message.includes('GlassesM4a') ||
      error.message.includes('not available in this native build'))
  );
}

export async function startGlassesM4aRecording(
  outputUri: string,
): Promise<GlassesM4aRecordingResult> {
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  return native.startGlassesM4aRecording(outputUri);
}

export async function stopGlassesM4aRecording(reason: string): Promise<GlassesM4aRecordingResult> {
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  return native.stopGlassesM4aRecording(reason);
}

export async function recoverGlassesM4aRecording(): Promise<GlassesM4aRecoveryResult> {
  const native = loadSdk();
  if (!native) return { recovered: false, outputUri: null };
  try {
    return await native.recoverGlassesM4aRecording();
  } catch (error) {
    if (isGlassesAudioRecorderUnavailable(error)) return { recovered: false, outputUri: null };
    throw error;
  }
}

export async function getGlassesM4aRecordingStatus(): Promise<GlassesM4aRecordingStatus> {
  const native = loadSdk();
  if (!native) return { recording: false, outputUri: null, startedAt: null };
  try {
    return await native.getGlassesM4aRecordingStatus();
  } catch (error) {
    if (isGlassesAudioRecorderUnavailable(error)) {
      return { recording: false, outputUri: null, startedAt: null };
    }
    throw error;
  }
}

export async function playGlassesM4aRecording(
  outputUri: string,
): Promise<{ playing: boolean; durationMs?: number }> {
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  return native.playGlassesM4aRecording(outputUri);
}

export async function stopGlassesM4aPlayback(): Promise<void> {
  const native = loadSdk();
  if (!native) return;
  await native.stopGlassesM4aPlayback();
}

export async function setMentraMicState(enabled: boolean): Promise<void> {
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  await native.setMicState(enabled, true, false, false);
}

export function subscribeGlassesM4aRecordingFinished(
  listener: (result: GlassesM4aRecordingResult) => void,
): () => void {
  const native = loadSdk();
  if (!native) return () => undefined;
  const subscription = native.addListener('glasses_audio_recording_finished', (event) => {
    if (event && typeof event.outputUri === 'string') listener(event as GlassesM4aRecordingResult);
  });
  return () => subscription.remove();
}

/**
 * The Bluetooth SDK deliberately does not request Android runtime permissions for callers.
 * Without these permissions Android's BLE scanner can fail silently inside the native SDK and
 * the scan promise resolves with an empty list, which looks exactly like "no glasses nearby".
 */
export async function ensureMentraBluetoothPermissions(): Promise<void> {
  if (Platform.OS !== 'android') {
    debugSdk('permissions_skipped', { platform: Platform.OS });
    return;
  }

  const required =
    Platform.Version >= 31
      ? [
          PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN,
          PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
        ]
      : [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
  const statuses = await PermissionsAndroid.requestMultiple(required);
  const denied = required.filter(
    (permission) => statuses[permission] !== PermissionsAndroid.RESULTS.GRANTED,
  );
  debugSdk('permissions_result', {
    platformVersion: Platform.Version,
    grantedCount: required.length - denied.length,
    requiredCount: required.length,
    deniedCount: denied.length,
  });
  if (denied.length === 0) return;

  if (Platform.Version >= 31) {
    throw new Error(
      'Nearby devices permission is required to find the Mentra Live glasses. Allow it in Android settings and retry.',
    );
  }
  throw new Error(
    'Location permission is required by Android to scan for the Mentra Live glasses. Allow it and retry.',
  );
}

export async function getDefaultGlassesDevice(): Promise<MentraDevice | null> {
  const native = loadSdk();
  if (!native) return null;
  const nativeDevice = await native.getDefaultDevice();
  if (nativeDevice) {
    debugSdk('default_device_native', { model: nativeDevice.model, present: true });
    return nativeDevice;
  }

  // The SDK's observable store is process-local. Restore our app-owned copy so
  // an ordinary app restart (or an APK update that recreates the native module)
  // does not require pairing again.
  const persisted = await loadPersistedDefaultDevice();
  if (persisted) {
    await native.setDefaultDevice(persisted);
    debugSdk('default_device_restored', { model: persisted.model, present: true });
    return persisted;
  }
  debugSdk('default_device_missing', { present: false });
  return null;
}

async function loadPersistedDefaultDevice(): Promise<MentraDevice | null> {
  try {
    const raw = await AsyncStorage.getItem(DEFAULT_DEVICE_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<MentraDevice>;
    if (
      typeof value.id !== 'string' ||
      typeof value.model !== 'string' ||
      typeof value.name !== 'string' ||
      !value.id ||
      !value.model ||
      !value.name
    ) {
      return null;
    }
    return {
      id: value.id,
      model: value.model,
      name: value.name,
      ...(typeof value.address === 'string' ? { address: value.address } : {}),
      ...(typeof value.rssi === 'number' ? { rssi: value.rssi } : {}),
    };
  } catch {
    return null;
  }
}

async function persistDefaultDevice(device: MentraDevice | null): Promise<void> {
  if (!device) {
    await AsyncStorage.removeItem(DEFAULT_DEVICE_STORAGE_KEY);
    return;
  }
  await AsyncStorage.setItem(DEFAULT_DEVICE_STORAGE_KEY, JSON.stringify(device));
}

export async function scanForGlasses(
  onResults?: (devices: MentraDevice[]) => void,
): Promise<MentraDevice[]> {
  debugSdk('scan_starting', { model: 'Mentra Live', timeoutMs: 15000 });
  await ensureMentraBluetoothPermissions();
  const native = loadSdk();
  if (!native) {
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  }
  try {
    const devices = await native.scan('Mentra Live', { timeoutMs: 15_000, onResults });
    debugSdk('scan_finished', {
      count: devices.length,
      models: devices.map((device) => device.model).filter(Boolean),
    });
    return devices;
  } catch (error) {
    debugSdk('scan_failed', { error: String(error) });
    throw error;
  }
}

async function waitForGlassesReady(timeoutMs = 20_000): Promise<void> {
  const native = loadInternalSdk();
  if (!native?.getGlassesStatus) {
    debugSdk('readiness_unavailable');
    return;
  }
  const isReady = (status: { connection?: { state?: string; fullyBooted?: boolean } }): boolean =>
    status.connection?.state === 'connected' && status.connection.fullyBooted === true;
  const initialStatus = await native.getGlassesStatus();
  debugSdk('readiness_status', { connection: initialStatus?.connection });
  if (isReady(initialStatus)) return;
  if (!native.onGlassesStatus) {
    debugSdk('readiness_listener_unavailable');
    throw new Error('Glasses connected but readiness status is unavailable.');
  }
  await new Promise<void>((resolve, reject) => {
    let unsubscribe: () => void = () => undefined;
    const timer = setTimeout(() => {
      unsubscribe();
      debugSdk('readiness_timeout', { timeoutMs });
      reject(new Error('Glasses connected but did not finish booting. Keep them awake and retry.'));
    }, timeoutMs);
    unsubscribe = native.onGlassesStatus!((status) => {
      debugSdk('readiness_status', { connection: status?.connection });
      if (!isReady(status)) return;
      clearTimeout(timer);
      unsubscribe();
      debugSdk('readiness_ready', { connection: status.connection });
      resolve();
    });
  });
}

export async function pairGlasses(device: MentraDevice): Promise<void> {
  debugSdk('pair_starting', { model: device.model });
  await ensureMentraBluetoothPermissions();
  const native = loadSdk();
  if (!native) {
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  }
  if (device.model !== 'Mentra Live') {
    throw new Error(`Unsupported glasses model: ${device.model}. Select a Mentra Live device.`);
  }
  try {
    await native.connect(device, { saveAsDefault: true, cancelExistingConnectionAttempt: true });
    await persistDefaultDevice(device);
    await setExpectedGlassesAlertAudioDevice(device.name?.trim() || null).catch(() => undefined);
    await waitForGlassesReady();
    await configureCaptureDefaults();
    debugSdk('pair_finished', { model: device.model });
  } catch (error) {
    debugSdk('pair_failed', { model: device.model, error: String(error) });
    throw error;
  }
}

export async function forgetPairedGlasses(): Promise<void> {
  const native = loadSdk();
  await persistDefaultDevice(null);
  if (!native) return;
  await native.clearDefaultDevice();
  await native.forget().catch(() => undefined);
  await setExpectedGlassesAlertAudioDevice(null).catch(() => undefined);
}

export function subscribeMentraEvents(onCaptureSignal: (kind: CaptureKind) => void): () => void {
  const native = loadSdk();
  if (!native) return () => undefined;
  const subscriptions = [
    native.addListener('wifi_status_change', (event) => {
      updateWifiState(event);
    }),
    native.addListener('hotspot_status_change', (event) => {
      updateHotspotState(event);
    }),
    native.addListener('photo_status', (event) => {
      debugSdk('capture_signal', { kind: 'photo', status: event?.status });
      // Intermediate accepted/configuring/capturing events can fire in bursts. Reconcile only
      // once the camera reports bytes are available or are being transferred; the periodic task
      // remains the backstop for a missed terminal event.
      if (['captured', 'ready_for_transfer', 'transferring'].includes(event?.status)) {
        onCaptureSignal('photo');
      }
    }),
    native.addListener('photo_response', (event) => {
      debugSdk('capture_signal', { kind: 'photo_response', state: event?.state });
      // Some firmware/SDK combinations only emit the terminal response for a
      // physical-button capture. Reconcile on either terminal outcome; the
      // manifest remains the source of truth for whether bytes are available.
      if (event?.state === 'success' || event?.state === 'error') onCaptureSignal('photo');
    }),
    native.addListener('media_success', () => {
      debugSdk('capture_signal', { kind: 'photo', source: 'media_success' });
      onCaptureSignal('photo');
    }),
    native.addListener('video_recording_status', (event) => {
      if (event?.status === 'recording_stopped') {
        debugSdk('capture_signal', { kind: 'video', status: event.status });
        onCaptureSignal('video');
      }
    }),
    native.addListener('gallery_status', () => {
      debugSdk('capture_signal', { kind: 'photo', source: 'gallery_status' });
      onCaptureSignal('photo');
    }),
  ];
  return () => subscriptions.forEach((subscription) => subscription.remove());
}

export function subscribeMentraAudioOutput(
  onAudioDevice: (deviceName: string | null) => void,
): () => void {
  const native = loadSdk();
  if (!native) return () => undefined;
  const subscriptions = [
    native.addListener('audio_connected', (event) => {
      const deviceName = typeof event?.deviceName === 'string' ? event.deviceName.trim() : '';
      if (deviceName) onAudioDevice(deviceName);
    }),
    native.addListener('audio_disconnected', () => onAudioDevice(null)),
  ];
  return () => subscriptions.forEach((subscription) => subscription.remove());
}

export function getKnownGlassesIp(): string | null {
  return wifiIp;
}

export function getKnownHotspot(): { localIp: string; ssid: string; password: string } | null {
  return hotspot;
}

export async function configureCaptureDefaults(): Promise<void> {
  const native = loadSdk();
  if (!native) return;
  const commands: [string, () => Promise<unknown>][] = [
    ['gallery_mode', () => native.setGalleryModeEnabled(true)],
    [
      'photo_defaults',
      () => native.setPhotoCaptureDefaults({ size: 'max', compress: 'medium', sound: true }),
    ],
    [
      'video_defaults',
      () => native.setVideoRecordingDefaults({ width: 1280, height: 720, fps: 30 }),
    ],
    ['max_video_duration', () => native.setMaxVideoRecordingDuration(15)],
  ];
  for (const [name, command] of commands) {
    debugSdk('capture_setting_starting', { name });
    try {
      const result = await command();
      debugSdk('capture_setting_succeeded', { name, result });
    } catch (error) {
      debugSdk('capture_setting_failed', { name, error: String(error) });
      throw error;
    }
  }
}

export async function ensureMentraConnection(
  options: { applyCaptureDefaults?: boolean } = {},
): Promise<boolean> {
  debugSdk('connection_ensure_starting', {
    applyCaptureDefaults: options.applyCaptureDefaults !== false,
  });
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  const defaultDevice = await getDefaultGlassesDevice();
  if (!defaultDevice) {
    debugSdk('connection_no_default_device');
    return false;
  }
  await ensureMentraBluetoothPermissions();
  if (defaultDevice.model !== 'Mentra Live') {
    throw new Error(
      `The saved glasses device is ${defaultDevice.model}. Pair a Mentra Live from Settings → Glasses capture.`,
    );
  }
  const internal = loadInternalSdk();
  let currentStatus = await internal?.getGlassesStatus?.();
  debugSdk('connection_status_before', {
    connection: currentStatus?.connection,
    deviceModel: currentStatus?.deviceModel,
    galleryModeEnabled: currentStatus?.galleryModeEnabled,
  });
  const alreadyReady =
    currentStatus?.connection?.state === 'connected' &&
    currentStatus.connection.fullyBooted === true;
  const nativeModel = currentStatus?.deviceModel?.trim();
  // A stale SDK controller can report a connected link while still holding a
  // controller for another device family. Camera/gallery commands then reject
  // with `unsupported_device`. Reconnect the persisted Mentra Live target once
  // so the native SGC is rebuilt before applying camera settings.
  const wrongNativeController = Boolean(nativeModel && nativeModel !== 'Mentra Live');
  if (!alreadyReady || wrongNativeController) {
    debugSdk('connection_reconnecting', { alreadyReady, wrongNativeController });
    if (wrongNativeController) {
      await native.connect(defaultDevice, {
        saveAsDefault: true,
        cancelExistingConnectionAttempt: true,
      });
    } else {
      await native.connectDefault({ cancelExistingConnectionAttempt: true });
    }
    await waitForGlassesReady();
    currentStatus = await internal?.getGlassesStatus?.();
    debugSdk('connection_status_after', {
      connection: currentStatus?.connection,
      deviceModel: currentStatus?.deviceModel,
      galleryModeEnabled: currentStatus?.galleryModeEnabled,
    });
  }
  await waitForGlassesReady();
  if (options.applyCaptureDefaults !== false) {
    try {
      await configureCaptureDefaults();
    } catch (error) {
      // The SDK can deliver the connection-ready event just before its SGC
      // reference is installed. Retry once after the native connection has
      // settled; this is specifically for gallery-mode activation and avoids
      // leaving the physical camera button inert after app startup.
      if (!String(error).includes('unsupported_device')) {
        debugSdk('connection_configuration_failed', { error: String(error) });
        throw error;
      }
      debugSdk('connection_configuration_retrying', { reason: 'unsupported_device' });
      await native.connect(defaultDevice, {
        saveAsDefault: true,
        cancelExistingConnectionAttempt: true,
      });
      await waitForGlassesReady();
      await configureCaptureDefaults();
    }
  }
  debugSdk('connection_ensure_succeeded', {
    applyCaptureDefaults: options.applyCaptureDefaults !== false,
  });
  return true;
}

export async function enableGlassesHotspot(): Promise<{ localIp: string; openedByUs: boolean }> {
  const native = loadSdk();
  if (!native) throw new Error('Mentra Bluetooth SDK is not available in this build.');
  if (hotspot?.localIp) {
    if (localNetwork?.connect) {
      await localNetwork.connect(hotspot.ssid, hotspot.password);
      scopedNetworkActive = true;
    }
    return { localIp: hotspot.localIp, openedByUs: false };
  }
  const state = await native.setHotspotState(true);
  updateHotspotState(state);
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (hotspot?.localIp) {
      if (localNetwork?.connect) {
        await localNetwork.connect(hotspot.ssid, hotspot.password);
        scopedNetworkActive = true;
      }
      return { localIp: hotspot.localIp, openedByUs: true };
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Glasses hotspot did not become available');
}

export async function disableGlassesHotspot(): Promise<void> {
  const native = loadSdk();
  if (scopedNetworkActive && localNetwork?.disconnect) {
    await localNetwork.disconnect().catch(() => undefined);
    scopedNetworkActive = false;
  }
  if (native && hotspot) await native.setHotspotState(false).catch(() => undefined);
  hotspot = null;
}

/** Release only the phone's scoped route, preserving a hotspot owned by another app/session. */
export async function releaseGlassesNetwork(): Promise<void> {
  if (scopedNetworkActive && localNetwork?.disconnect) {
    await localNetwork.disconnect().catch(() => undefined);
    scopedNetworkActive = false;
  }
}

export async function fetchGlassesUrl(url: string, init?: RequestInit): Promise<Response> {
  if (!scopedNetworkActive || !localNetwork?.request) return fetch(url, init);
  const requestId = `capture_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const headers: Record<string, string> = {};
  new Headers(init?.headers).forEach((value, key) => {
    headers[key] = value;
  });
  const result = await localNetwork.request(
    requestId,
    url,
    init?.method ?? 'GET',
    headers,
    typeof init?.body === 'string' ? init.body : null,
    30_000,
  );
  return new Response(Buffer.from(result.bodyBase64, 'base64') as unknown as BodyInit, {
    status: result.status,
    headers: result.headers,
  });
}

/**
 * Stream a camera-server file directly to disk when the scoped hotspot transport is active.
 * The request() bridge returns base64 and is intentionally limited to small control responses;
 * using it for a video would retain the complete media (and its base64 expansion) in JS memory.
 */
export async function downloadGlassesFile(url: string, destinationUri: string): Promise<number> {
  if (!scopedNetworkActive || !localNetwork?.download) {
    const result = await FileSystem.downloadAsync(url, destinationUri);
    if (result.status < 200 || result.status >= 300) {
      throw new Error(`Glasses download failed (${result.status})`);
    }
    const info = await FileSystem.getInfoAsync(destinationUri);
    return info.exists && 'size' in info ? (info.size ?? 0) : 0;
  }

  // MentraLocalNetwork is implemented with java.io.File on Android and therefore expects a
  // filesystem path, while Expo exposes file:/// URIs to JavaScript.
  const destinationPath = decodeURIComponent(destinationUri.replace(/^file:\/\//, ''));
  const requestId = `capture_download_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const result = await localNetwork.download(requestId, url, destinationPath, {}, 30_000, 120_000);
  if (result.statusCode < 200 || result.statusCode >= 300) {
    throw new Error(`Glasses download failed (${result.statusCode})`);
  }
  return result.bytesWritten;
}
