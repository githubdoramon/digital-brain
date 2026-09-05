import type { CaptureKind } from './types';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Buffer } from 'buffer';
import * as FileSystem from 'expo-file-system/legacy';
import { PermissionsAndroid, Platform } from 'react-native';

import { appendMentraDebugLog, appendWakeCommandDebugLog } from './debug';
import { setExpectedGlassesAlertAudioDevice } from '@/glassesAlerts/runtime';

type Subscription = { remove: () => void };
type PhotoRequestParams = {
  requestId?: string;
  size: 'low' | 'medium' | 'high' | 'max';
  mode?: 'photo' | 'text';
  transferMethod?: 'auto' | 'direct' | 'ble';
  webhookUrl: string | null;
  authToken: string | null;
  compress: 'none' | 'medium' | 'heavy';
  save?: boolean;
  sound: boolean;
};
type PhotoSuccessResponseEvent = { state: 'success'; requestId?: string } & Record<string, unknown>;
export type MentraDevice = {
  id: string;
  model: string;
  name: string;
  address?: string;
  rssi?: number;
};
export type MentraConnectionStatus = {
  hasSavedDevice: boolean;
  connected: boolean;
  fullyBooted: boolean;
  state: string | null;
  deviceModel?: string | null;
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
  disconnect: () => Promise<void>;
  forget: () => Promise<void>;
  setGalleryModeEnabled: (enabled: boolean) => Promise<unknown>;
  requestPhoto: (params: PhotoRequestParams) => Promise<PhotoSuccessResponseEvent>;
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
  rgbLedControl: (
    requestId: string,
    packageName: string | null,
    action: 'on' | 'off',
    color: 'red' | 'green' | 'blue' | 'orange' | 'white' | null,
    onDurationMs: number,
    offDurationMs: number,
    count: number,
  ) => Promise<{ requestId: string }>;
  dispatchRgbLedControl?: (params: {
    requestId: string;
    action: 'on' | 'off';
    color: 'red' | 'green' | 'blue' | 'orange' | 'white' | null;
    onDurationMs: number;
    offDurationMs: number;
    count: number;
  }) => string;
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

export type MentraMicPcm = {
  pcm: ArrayBuffer | ArrayBufferView;
  sampleRate: 16_000;
  bitsPerSample: 16;
  channels: 1;
  encoding: 'pcm_s16le';
};

export type MentraVideoRecordingStatus = {
  status?: string;
  data?: { recording?: boolean };
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

type DirectRgbLedModule = {
  dispatchRgbLedControl?: BluetoothSdk['dispatchRgbLedControl'];
};

function loadDirectRgbLedDispatcher(): BluetoothSdk['dispatchRgbLedControl'] {
  try {
    // The public SDK facade deliberately exposes only the response-tracked LED
    // command. Load the Expo native module directly for our latency-sensitive
    // fire-and-forget acknowledgement.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const expoModules = require('expo-modules-core') as {
      requireNativeModule?: (name: string) => DirectRgbLedModule;
    };
    const nativeModule = expoModules.requireNativeModule?.('BluetoothSdk');
    const dispatcher = nativeModule?.dispatchRgbLedControl;
    return typeof dispatcher === 'function' ? dispatcher.bind(nativeModule) : undefined;
  } catch {
    return undefined;
  }
}

const DEFAULT_DEVICE_STORAGE_KEY = 'digitalbrain.mentra.default.device.v1';

type LocalNetworkModule = {
  addListener?: (event: string, listener: (event: any) => void) => Subscription;
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
let immediateRgbLedDispatcher: BluetoothSdk['dispatchRgbLedControl'];
let wifiIp: string | null = null;
let hotspot: { localIp: string; ssid: string; password: string } | null = null;
let localNetwork: LocalNetworkModule | null = null;
let localNetworkListenerInitialized = false;
let scopedNetworkActive = false;
let stateListenersInitialized = false;
let diagnosticsListenersInitialized = false;
let lastNativeLogAt = 0;
let activeMentraConnection: Promise<boolean> | null = null;
let activeMentraConnectionAppliesCaptureDefaults = false;
const automaticPhotoRequestIds = new Set<string>();

function debugSdk(event: string, payload?: unknown): void {
  void appendMentraDebugLog(event, payload).catch(() => undefined);
  const nativeMessage =
    event === 'sdk_event' &&
    payload &&
    typeof payload === 'object' &&
    typeof (payload as { payload?: { message?: unknown } }).payload?.message === 'string'
      ? (payload as { payload: { message: string } }).payload.message
      : null;
  if (event.startsWith('wake_led_') || nativeMessage?.includes('RGB LED control')) {
    void appendWakeCommandDebugLog(event, payload).catch(() => undefined);
  }
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
    immediateRgbLedDispatcher = loadDirectRgbLedDispatcher();
    if (immediateRgbLedDispatcher) {
      debugSdk('wake_led_transport_ready', { transport: 'direct_native_module' });
    }
    try {
      // The published SDK keeps the Android scoped-network bridge on its internal export.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const internal = require('@mentra/bluetooth-sdk/internal') as {
        default?: { dispatchRgbLedControl?: BluetoothSdk['dispatchRgbLedControl'] };
        MentraLocalNetwork?: LocalNetworkModule;
      };
      const dispatchRgbLedControl = internal.default?.dispatchRgbLedControl;
      if (!immediateRgbLedDispatcher && dispatchRgbLedControl) {
        immediateRgbLedDispatcher = dispatchRgbLedControl.bind(internal.default);
        debugSdk('wake_led_transport_ready', { transport: 'sdk_internal' });
      }
      localNetwork = internal.MentraLocalNetwork ?? null;
      if (localNetwork?.addListener && !localNetworkListenerInitialized) {
        localNetwork.addListener('networkLost', (event) => {
          scopedNetworkActive = false;
          debugSdk('glasses_local_network_lost', {
            transport: 'glasses_hotspot',
            network_lost: true,
            hasSsid: typeof event?.ssid === 'string' && event.ssid.length > 0,
          });
        });
        localNetworkListenerInitialized = true;
      }
    } catch {
      localNetwork = null;
    }
    if (!immediateRgbLedDispatcher) {
      debugSdk('wake_led_transport_unavailable', {
        reason: 'native_dispatch_function_not_exposed',
      });
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
  } catch (error) {
    sdk = null;
    immediateRgbLedDispatcher = undefined;
    debugSdk('sdk_load_error', {
      error: error instanceof Error ? error.message : String(error),
      error_name: error instanceof Error ? error.name : null,
    });
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

async function blinkMentraLed(
  color: 'blue' | 'orange' | 'red',
  requestPrefix: 'wake' | 'command-finished' | 'command-error',
): Promise<string> {
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  const requestId = `digitalbrain-${requestPrefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const startedAt = Date.now();
  debugSdk('wake_led_request', { request_id: requestId, color, count: 1 });
  if (immediateRgbLedDispatcher) {
    try {
      immediateRgbLedDispatcher({
        requestId,
        action: 'on',
        color,
        onDurationMs: 250,
        offDurationMs: 0,
        count: 1,
      });
      debugSdk('wake_led_dispatched', {
        request_id: requestId,
        color,
        dispatch_ms: Date.now() - startedAt,
        transport: 'immediate',
      });
      return requestId;
    } catch (error) {
      debugSdk('wake_led_direct_dispatch_failed', {
        request_id: requestId,
        color,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  debugSdk('wake_led_dispatch_fallback', {
    request_id: requestId,
    color,
    reason: 'direct_native_dispatch_unavailable_or_failed',
  });
  const response = await native.rgbLedControl(requestId, null, 'on', color, 250, 0, 1);
  debugSdk('wake_led_acknowledged', {
    request_id: response.requestId,
    color,
    acknowledgement_ms: Date.now() - startedAt,
  });
  return response.requestId;
}

export function blinkMentraBlueLed(): Promise<string> {
  return blinkMentraLed('blue', 'wake');
}

export function blinkMentraOrangeLed(): Promise<string> {
  return blinkMentraLed('orange', 'command-finished');
}

export function blinkMentraRedLed(): Promise<string> {
  return blinkMentraLed('red', 'command-error');
}

function isArrayBuffer(value: unknown): value is ArrayBuffer {
  // Native event payloads may originate from another JavaScript realm, where
  // `instanceof ArrayBuffer` is false despite a valid ArrayBuffer payload.
  return Object.prototype.toString.call(value) === '[object ArrayBuffer]';
}

function isPcmBuffer(value: unknown): value is ArrayBuffer | ArrayBufferView {
  return isArrayBuffer(value) || ArrayBuffer.isView(value);
}

export function subscribeMentraMicPcm(listener: (event: MentraMicPcm) => void): () => void {
  const native = loadSdk();
  if (!native) return () => undefined;
  const subscription = native.addListener('mic_pcm', (event) => {
    if (
      isPcmBuffer(event?.pcm) &&
      event.sampleRate === 16_000 &&
      event.bitsPerSample === 16 &&
      event.channels === 1 &&
      event.encoding === 'pcm_s16le'
    ) {
      listener(event as MentraMicPcm);
      return;
    }
    debugSdk('wake_pcm_invalid', {
      sample_rate: event?.sampleRate,
      bits_per_sample: event?.bitsPerSample,
      channels: event?.channels,
      encoding: event?.encoding,
      pcm_type: Object.prototype.toString.call(event?.pcm),
    });
  });
  return () => subscription.remove();
}

export function subscribeMentraVideoRecordingStatus(
  listener: (event: MentraVideoRecordingStatus) => void,
): () => void {
  const native = loadSdk();
  if (!native) return () => undefined;
  const subscription = native.addListener('video_recording_status', listener);
  return () => subscription.remove();
}

export function subscribeMentraConnectionState(
  listener: (status: MentraConnectionStatus) => void,
): () => void {
  const native = loadInternalSdk();
  if (!native?.getGlassesStatus) return () => undefined;
  const report = (status?: { connection?: { state?: string; fullyBooted?: boolean } }) => {
    const state = status?.connection?.state ?? null;
    const fullyBooted = status?.connection?.fullyBooted === true;
    void getDefaultGlassesDevice()
      .then((device) =>
        listener({
          hasSavedDevice: device !== null,
          connected: state === 'connected' && fullyBooted,
          fullyBooted,
          state,
        }),
      )
      .catch(() => undefined);
  };
  void native
    .getGlassesStatus()
    .then(report)
    .catch(() => undefined);
  return native.onGlassesStatus?.(report) ?? (() => undefined);
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

/**
 * A saved SDK default is not proof of a live BLE session. Settings uses this
 * read-only snapshot so it never labels stale pairing data as connected.
 */
export async function getMentraConnectionStatus(): Promise<MentraConnectionStatus> {
  const device = await getDefaultGlassesDevice();
  const native = loadInternalSdk();
  const status = await native?.getGlassesStatus?.();
  const state = status?.connection?.state ?? null;
  const fullyBooted = status?.connection?.fullyBooted === true;
  return {
    hasSavedDevice: device !== null,
    connected: state === 'connected' && fullyBooted,
    fullyBooted,
    state,
    deviceModel: status?.deviceModel?.trim() || null,
  };
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

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

async function waitForGlassesReady(timeoutMs = 30_000): Promise<void> {
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

function isBootingConnection(
  status:
    | {
        connection?: { state?: string; fullyBooted?: boolean };
      }
    | undefined,
): boolean {
  const state = status?.connection?.state;
  return (
    state === 'scanning' || state === 'connecting' || state === 'bonding' || state === 'connected'
  );
}

async function resetAndReconnectMentra(
  native: BluetoothSdk,
  device: MentraDevice,
  reason: 'stalled_boot' | 'wrong_controller',
): Promise<void> {
  debugSdk('connection_reset_starting', { reason });
  // Do not issue connect-with-cancel immediately after disconnect. The SDK's
  // cancellation path closes the GATT link asynchronously; starting a second
  // scan before Android has released it can leave Mentra in connected-but-not-
  // fully-booted state until the glasses are power-cycled.
  await native.disconnect().catch(() => undefined);
  await wait(1_000);
  await native.connect(device, { saveAsDefault: true, cancelExistingConnectionAttempt: false });
  await waitForGlassesReady();
  debugSdk('connection_reset_succeeded', { reason });
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
    // A user-selected pair operation deliberately replaces the active target,
    // but it still waits for a foreground/sync ensure to finish first. That
    // makes Digital Brain the single owner of controller changes in this
    // process instead of letting a pair and an automatic reconnect collide.
    await activeMentraConnection?.catch(() => undefined);
    await native.disconnect().catch(() => undefined);
    await wait(1_000);
    await native.connect(device, { saveAsDefault: true, cancelExistingConnectionAttempt: false });
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
      if (isAutomaticPhotoEvent(event)) return;
      debugSdk('capture_signal', { kind: 'photo', status: event?.status });
      // Intermediate accepted/configuring/capturing events can fire in bursts. Reconcile only
      // once the camera reports bytes are available or are being transferred; the periodic task
      // remains the backstop for a missed terminal event.
      if (['captured', 'ready_for_transfer', 'transferring'].includes(event?.status)) {
        onCaptureSignal('photo');
      }
    }),
    native.addListener('photo_response', (event) => {
      if (isAutomaticPhotoEvent(event)) return;
      debugSdk('capture_signal', { kind: 'photo_response', state: event?.state });
      // Some firmware/SDK combinations only emit the terminal response for a
      // physical-button capture. Reconcile on either terminal outcome; the
      // manifest remains the source of truth for whether bytes are available.
      if (event?.state === 'success' || event?.state === 'error') onCaptureSignal('photo');
    }),
    native.addListener('media_success', (event) => {
      if (isAutomaticPhotoEvent(event)) return;
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

export function registerAutomaticPhotoRequest(requestId: string): void {
  automaticPhotoRequestIds.add(requestId);
}

export function unregisterAutomaticPhotoRequest(requestId: string): void {
  automaticPhotoRequestIds.delete(requestId);
}

function isAutomaticPhotoEvent(event: any): boolean {
  return typeof event?.requestId === 'string' && automaticPhotoRequestIds.has(event.requestId);
}

/**
 * Request a one-shot photo from the glasses. This is deliberately separate from
 * gallery-mode/button capture: callers can set save=false so the image is
 * delivered only to their explicitly supplied receiver and never enters the
 * normal Immich reconciliation queue.
 */
export async function requestGlassesPhoto(
  params: PhotoRequestParams,
): Promise<PhotoSuccessResponseEvent> {
  const native = loadSdk();
  if (!native?.requestPhoto) {
    throw new Error(
      'Mentra photo capture is not available in this build. Rebuild the Android app.',
    );
  }
  return native.requestPhoto(params);
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

async function ensureMentraConnectionOnce(
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
    // A normal Android resume can observe the SDK while it is still bonding or
    // finishing its control-plane boot. Let that single attempt settle before
    // resetting it; reconnecting with cancelExisting at this point is exactly
    // what turns a transient boot into a permanent-looking timeout.
    if (!wrongNativeController && isBootingConnection(currentStatus)) {
      try {
        await waitForGlassesReady();
      } catch {
        await resetAndReconnectMentra(native, defaultDevice, 'stalled_boot');
      }
    } else {
      if (wrongNativeController) {
        await resetAndReconnectMentra(native, defaultDevice, 'wrong_controller');
      } else {
        // Match Mentra's own reconnect behavior: an idle/disconnected SDK can
        // simply connect its stored default. Avoid a needless disconnect on
        // every cold start, which creates an additional race with Android BLE.
        await native.connectDefault({ cancelExistingConnectionAttempt: false });
        await waitForGlassesReady();
      }
    }
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
      await resetAndReconnectMentra(native, defaultDevice, 'wrong_controller');
      await configureCaptureDefaults();
    }
  }
  debugSdk('connection_ensure_succeeded', {
    applyCaptureDefaults: options.applyCaptureDefaults !== false,
  });
  return true;
}

/**
 * The Mentra SDK owns one controller per process. App launch, foreground
 * resume, manual Connect, and capture sync can all need that controller, but
 * they must join one operation rather than repeatedly cancel each other.
 */
export async function ensureMentraConnection(
  options: { applyCaptureDefaults?: boolean } = {},
): Promise<boolean> {
  const applyCaptureDefaults = options.applyCaptureDefaults !== false;
  if (activeMentraConnection) {
    debugSdk('connection_joined_existing_attempt', { applyCaptureDefaults });
    const joined = activeMentraConnection;
    if (!applyCaptureDefaults || activeMentraConnectionAppliesCaptureDefaults) return joined;
    // A sync can start first with defaults disabled because a camera operation
    // is in flight. If the foreground owner then needs defaults, run one
    // follow-up after the shared connection completes instead of interrupting
    // it in the middle of boot.
    return joined.then(async (connected) => {
      if (!connected) return false;
      return ensureMentraConnection({ applyCaptureDefaults: true });
    });
  }

  activeMentraConnectionAppliesCaptureDefaults = applyCaptureDefaults;
  const operation = ensureMentraConnectionOnce({ applyCaptureDefaults });
  activeMentraConnection = operation.finally(() => {
    activeMentraConnection = null;
    activeMentraConnectionAppliesCaptureDefaults = false;
  });
  return activeMentraConnection;
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
