import type { CaptureKind } from './types';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Buffer } from 'buffer';
import * as FileSystem from 'expo-file-system/legacy';
import { PermissionsAndroid, Platform } from 'react-native';

import { appendMentraDebugLog, appendWakeCommandDebugLog } from './debug';
import { assertGlassesNotUpdating, isGlassesMaintenanceActive } from './maintenance';
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
  requestWifiScan: () => Promise<unknown[]>;
  sendWifiCredentials: (ssid: string, password: string) => Promise<unknown>;
  forgetWifiNetwork: (ssid: string) => Promise<unknown>;
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
const GLASSES_WIFI_STORAGE_KEY = 'digitalbrain.mentra.glasses.wifi.credentials.v1';
const GLASSES_WIFI_AUTO_SYNC_ENABLED_KEY = 'digitalbrain.mentra.glasses.wifi.auto_sync_enabled.v1';

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
let wifiSsid: string | null = null;
let hotspot: { localIp: string; ssid: string; password: string } | null = null;
let localNetwork: LocalNetworkModule | null = null;
let localNetworkListenerInitialized = false;
let scopedNetworkActive = false;
let stateListenersInitialized = false;
let diagnosticsListenersInitialized = false;
let lastNativeLogAt = 0;
let activeMentraConnection: Promise<boolean> | null = null;
let activeMentraConnectionAppliesCaptureDefaults = false;
let pendingConnectionRecovery: 'heartbeat_timeout' | 'manual_recovery' | null = null;
const automaticRecoveryAttempts: number[] = [];
const automaticPhotoRequestIds = new Set<string>();

export type GlassesWifiCredential = {
  ssid: string;
  password: string;
  createdAtMs: number;
  updatedAtMs: number;
  lastAttemptAtMs?: number;
  lastResult?: 'success' | 'failure';
};

type WifiCredentialsSyncResult = {
  state?: string;
  error?: string;
  success?: boolean;
  connected?: boolean;
};

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
  'ota_start_ack',
  'ota_status',
] as const;

function initializeDiagnosticsListeners(native: BluetoothSdk): void {
  if (diagnosticsListenersInitialized) return;
  diagnosticsListenersInitialized = true;
  DIAGNOSTIC_SDK_EVENTS.forEach((eventName) => {
    try {
      native.addListener(eventName, (payload) => {
        if (eventName === 'log') {
          const now = Date.now();
          const message = typeof payload?.message === 'string' ? payload.message : '';
          const lifecycle =
            /heartbeat|pong|GATT write failed|ACK timeout|glasses_ready|session changed|SOC.*off/i.test(
              message,
            );
          if (!lifecycle && now - lastNativeLogAt < 100) return;
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
  const internal = loadInternalSdk();
  // These lifecycle signals live on the SDK's internal facade, alongside status.
  const listenInternal = (event: string, listener: (payload: any) => void) => {
    try {
      internal?.addListener(event, listener);
    } catch (error) {
      debugSdk('sdk_listener_error', { sdkEvent: event, error: String(error) });
    }
  };
  listenInternal('glasses_session_changed', () => {
    debugSdk('glasses_process_restarted', { bluetooth_link_retained: true });
  });
  listenInternal('glasses_control_ready', (payload) => {
    debugSdk('glasses_control_ready', payload);
  });
  listenInternal('glasses_link_unhealthy', (payload) => {
    debugSdk('glasses_link_unhealthy', payload);
    if (isGlassesMaintenanceActive()) return;
    const now = Date.now();
    while (automaticRecoveryAttempts.length && now - automaticRecoveryAttempts[0] > 15 * 60_000) {
      automaticRecoveryAttempts.shift();
    }
    if (automaticRecoveryAttempts.length >= 2) {
      debugSdk('connection_recovery_rate_limited', { attempts: automaticRecoveryAttempts.length });
      return;
    }
    automaticRecoveryAttempts.push(now);
    pendingConnectionRecovery = 'heartbeat_timeout';
    void ensureMentraConnection().catch((error) => {
      debugSdk('connection_recovery_failed', { error: String(error) });
    });
  });
}

function updateWifiState(event: any): void {
  wifiIp = event?.state === 'connected' && typeof event.localIp === 'string' ? event.localIp : null;
  wifiSsid = event?.state === 'connected' && typeof event.ssid === 'string' ? event.ssid : null;
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
    // the public command surface, plus app-patched native events such as playback completion.
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
  await assertGlassesNotUpdating();
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
  if (enabled) await assertGlassesNotUpdating();
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

export function subscribeGlassesM4aPlaybackFinished(listener: (uri: string) => void): () => void {
  // This app-patched event is not in the published facade's public event allowlist.
  const native = loadInternalSdk();
  if (!native) return () => undefined;
  const subscription = native.addListener('glasses_audio_playback_finished', (event) => {
    if (event && typeof event.outputUri === 'string') listener(event.outputUri);
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

async function loadWifiCredentials(): Promise<Record<string, GlassesWifiCredential>> {
  try {
    const raw = await AsyncStorage.getItem(GLASSES_WIFI_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, GlassesWifiCredential>;
    if (!parsed || typeof parsed !== 'object') return {};

    const normalized: Record<string, GlassesWifiCredential> = {};
    for (const [ssid, value] of Object.entries(parsed)) {
      if (
        typeof value?.ssid !== 'string' ||
        typeof value?.password !== 'string' ||
        value.ssid.trim().length === 0
      ) {
        continue;
      }
      normalized[ssid.trim()] = {
        ssid: ssid.trim(),
        password: value.password,
        createdAtMs:
          typeof value.createdAtMs === 'number' && Number.isFinite(value.createdAtMs)
            ? value.createdAtMs
            : Date.now(),
        updatedAtMs:
          typeof value.updatedAtMs === 'number' && Number.isFinite(value.updatedAtMs)
            ? value.updatedAtMs
            : Date.now(),
        lastAttemptAtMs:
          typeof value.lastAttemptAtMs === 'number' && Number.isFinite(value.lastAttemptAtMs)
            ? value.lastAttemptAtMs
            : undefined,
        lastResult:
          value.lastResult === 'failure'
            ? 'failure'
            : value.lastResult === 'success'
              ? 'success'
              : undefined,
      };
    }
    return normalized;
  } catch {
    return {};
  }
}

async function saveWifiCredentials(
  credentials: Record<string, GlassesWifiCredential>,
): Promise<void> {
  const cleaned: Record<string, GlassesWifiCredential> = {};
  for (const [ssid, value] of Object.entries(credentials)) {
    if (typeof value?.ssid !== 'string' || value.ssid.trim().length === 0) continue;
    cleaned[ssid.trim()] = {
      ssid: value.ssid.trim(),
      password: value.password,
      createdAtMs: value.createdAtMs,
      updatedAtMs: value.updatedAtMs,
      ...(value.lastAttemptAtMs ? { lastAttemptAtMs: value.lastAttemptAtMs } : {}),
      ...(value.lastResult ? { lastResult: value.lastResult } : {}),
    };
  }
  await AsyncStorage.setItem(GLASSES_WIFI_STORAGE_KEY, JSON.stringify(cleaned));
}

async function getAutoWifiSyncEnabled(): Promise<boolean> {
  const raw = await AsyncStorage.getItem(GLASSES_WIFI_AUTO_SYNC_ENABLED_KEY);
  if (raw === null) return true;
  return raw === '1';
}

async function setAutoWifiSyncEnabled(enabled: boolean): Promise<void> {
  await AsyncStorage.setItem(GLASSES_WIFI_AUTO_SYNC_ENABLED_KEY, enabled ? '1' : '0');
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
  reason: 'stalled_boot' | 'wrong_controller' | 'heartbeat_timeout' | 'manual_recovery',
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
  await assertGlassesNotUpdating();
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
  await ownMentraConnection(async () => {
    try {
      await native.disconnect().catch(() => undefined);
      await wait(1_000);
      await native.connect(device, { saveAsDefault: true, cancelExistingConnectionAttempt: false });
      await persistDefaultDevice(device);
      await setExpectedGlassesAlertAudioDevice(device.name?.trim() || null).catch(() => undefined);
      await waitForGlassesReady();
      await configureCaptureDefaults();
      debugSdk('pair_finished', { model: device.model });
      return true;
    } catch (error) {
      debugSdk('pair_failed', { model: device.model, error: String(error) });
      throw error;
    }
  });
}

/** Reserve ownership for the entire mutation, including Android's GATT release delay. */
async function ownMentraConnection(
  action: () => Promise<boolean>,
  firmwareStatusOnly = false,
): Promise<boolean> {
  while (activeMentraConnection) await activeMentraConnection.catch(() => undefined);
  if (!firmwareStatusOnly) await assertGlassesNotUpdating();
  // Another caller can acquire the owner while the maintenance read yields.
  if (activeMentraConnection) return ownMentraConnection(action, firmwareStatusOnly);
  activeMentraConnectionAppliesCaptureDefaults = true;
  activeMentraConnection = Promise.resolve()
    .then(action)
    .finally(() => {
      activeMentraConnection = null;
      activeMentraConnectionAppliesCaptureDefaults = false;
    });
  return activeMentraConnection;
}

export async function forgetPairedGlasses(): Promise<void> {
  await ownMentraConnection(async () => {
    const native = loadSdk();
    pendingConnectionRecovery = null;
    await persistDefaultDevice(null);
    if (native) {
      await native.clearDefaultDevice();
      await native.forget().catch(() => undefined);
    }
    await setExpectedGlassesAlertAudioDevice(null).catch(() => undefined);
    return false;
  });
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

export function getKnownGlassesWifiSsid(): string | null {
  return wifiSsid;
}

export async function isAutoWifiSyncEnabled(): Promise<boolean> {
  return getAutoWifiSyncEnabled();
}

export async function setAutoWifiSyncEnabledForGlasses(enabled: boolean): Promise<void> {
  await setAutoWifiSyncEnabled(enabled);
}

export async function listGlassesWifiCredentials(): Promise<GlassesWifiCredential[]> {
  const credentials = await loadWifiCredentials();
  return Object.values(credentials).sort((a, b) => b.updatedAtMs - a.updatedAtMs);
}

export async function upsertGlassesWifiCredential(ssid: string, password: string): Promise<void> {
  const trimmedSsid = ssid.trim();
  if (!trimmedSsid) {
    throw new Error('Wi-Fi SSID is required.');
  }
  const credentials = await loadWifiCredentials();
  const now = Date.now();
  const previous = credentials[trimmedSsid];
  credentials[trimmedSsid] = {
    ssid: trimmedSsid,
    password,
    createdAtMs: previous?.createdAtMs ?? now,
    updatedAtMs: now,
  };
  await saveWifiCredentials(credentials);
  debugSdk('glasses_wifi_credentials_updated', {
    ssid: trimmedSsid,
    action: previous ? 'upserted' : 'saved',
  });
}

export async function removeGlassesWifiCredential(ssid: string): Promise<void> {
  const trimmedSsid = ssid.trim();
  const credentials = await loadWifiCredentials();
  delete credentials[trimmedSsid];
  await saveWifiCredentials(credentials);
  void debugSdk('glasses_wifi_credentials_removed', { ssid: trimmedSsid });
}

type SyncResult =
  | { status: 'disabled' }
  | { status: 'noop'; reason: string }
  | { status: 'success'; ssid: string }
  | { status: 'failed'; reason: string; ssid: string };

export async function syncSavedGlassesWifiCredentials(options?: {
  targetSsid?: string;
  scanNetworks?: boolean;
}): Promise<SyncResult> {
  await assertGlassesNotUpdating();
  const enabled = await getAutoWifiSyncEnabled();
  if (!enabled) return { status: 'disabled' };

  const native = loadSdk();
  if (!native?.sendWifiCredentials || !native?.requestWifiScan) {
    return { status: 'noop', reason: 'Wifi sync not supported by SDK in this build.' };
  }

  const credentials = await loadWifiCredentials();
  const trimmedTarget = options?.targetSsid?.trim();
  const candidates = Object.values(credentials);
  if (candidates.length === 0) {
    return { status: 'noop', reason: 'No Wi-Fi credentials saved.' };
  }

  const tryTargets: string[] = [];
  if (trimmedTarget) {
    if (credentials[trimmedTarget]) {
      tryTargets.push(trimmedTarget);
    } else {
      return { status: 'noop', reason: `No saved credentials for ${trimmedTarget}.` };
    }
  } else if (wifiSsid && credentials[wifiSsid]) {
    tryTargets.push(wifiSsid);
  } else if (options?.scanNetworks !== false) {
    try {
      const found = await native.requestWifiScan();
      if (Array.isArray(found)) {
        const scanOrder = found
          .map((network: any) => (typeof network?.ssid === 'string' ? network.ssid.trim() : ''))
          .filter(Boolean);
        const matching = [...new Set(scanOrder.filter((ssid) => ssid in credentials))];
        if (matching.length > 0) {
          tryTargets.push(...matching);
        }
      }
    } catch (error) {
      debugSdk('glasses_wifi_scan_failed', {
        error: error instanceof Error ? error.message : String(error),
      });
    }
    if (tryTargets.length === 0 && wifiSsid && credentials[wifiSsid]) {
      tryTargets.push(wifiSsid);
    }

    if (tryTargets.length === 0) {
      const ordered = Object.values(credentials).sort((a, b) => b.updatedAtMs - a.updatedAtMs);
      tryTargets.push(...ordered.map((entry) => entry.ssid));
    }
  }

  const uniqueTargets = Array.from(new Set(tryTargets));
  if (uniqueTargets.length === 0) {
    return { status: 'noop', reason: 'No matching credential found for current Wi-Fi network.' };
  }

  for (const ssid of uniqueTargets) {
    const credential = credentials[ssid];
    if (!credential) continue;
    const attemptAt = Date.now();
    const nextCredentials = await loadWifiCredentials();
    nextCredentials[ssid] = {
      ...nextCredentials[ssid],
      lastAttemptAtMs: attemptAt,
      lastResult: undefined,
    };
    await saveWifiCredentials(nextCredentials);
    try {
      const result = (await native.sendWifiCredentials(
        ssid,
        credential.password,
      )) as WifiCredentialsSyncResult;
      const state = typeof result?.state === 'string' ? result.state.toLowerCase() : null;
      const connected =
        state === 'connected' ||
        state === 'success' ||
        result?.connected === true ||
        result?.success === true;
      const now = Date.now();
      const updated = await loadWifiCredentials();
      updated[ssid] = {
        ...updated[ssid],
        lastAttemptAtMs: now,
        lastResult: connected ? 'success' : 'failure',
      };
      await saveWifiCredentials(updated);
      if (connected) {
        debugSdk('glasses_wifi_credentials_synced', { ssid, success: true });
        return { status: 'success', ssid };
      }
      debugSdk('glasses_wifi_credentials_synced', {
        ssid,
        success: false,
        reason: result?.error || 'glasses_not_connected',
      });
      continue;
    } catch (error) {
      const updated = await loadWifiCredentials();
      updated[ssid] = {
        ...updated[ssid],
        lastAttemptAtMs: attemptAt,
        lastResult: 'failure',
      };
      await saveWifiCredentials(updated);
      debugSdk('glasses_wifi_credentials_synced', {
        ssid,
        success: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  return {
    status: 'failed',
    ssid: uniqueTargets[uniqueTargets.length - 1],
    reason: 'No candidate SSID was accepted by glasses.',
  };
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
  await assertGlassesNotUpdating();
  const native = loadSdk();
  if (!native?.requestPhoto) {
    throw new Error(
      'Mentra photo capture is not available in this build. Rebuild the Android app.',
    );
  }
  return native.requestPhoto(params);
}

export async function configureCaptureDefaults(): Promise<void> {
  await assertGlassesNotUpdating();
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
  const recoveryReason = pendingConnectionRecovery;
  pendingConnectionRecovery = null;
  if (recoveryReason) {
    await resetAndReconnectMentra(native, defaultDevice, recoveryReason);
  } else if (!alreadyReady || wrongNativeController) {
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
  await assertGlassesNotUpdating();
  const applyCaptureDefaults = options.applyCaptureDefaults !== false;
  if (activeMentraConnection) {
    debugSdk('connection_joined_existing_attempt', { applyCaptureDefaults });
    const joined = activeMentraConnection;
    if (
      !pendingConnectionRecovery &&
      (!applyCaptureDefaults || activeMentraConnectionAppliesCaptureDefaults)
    )
      return joined;
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

/** An explicit repair releases a stale GATT session without forgetting pairing or rebooting. */
export async function recoverMentraConnection(): Promise<boolean> {
  await assertGlassesNotUpdating();
  pendingConnectionRecovery = 'manual_recovery';
  return ensureMentraConnection();
}

export async function enableGlassesHotspot(): Promise<{ localIp: string; openedByUs: boolean }> {
  await assertGlassesNotUpdating();
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
  await assertGlassesNotUpdating();
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

export type GlassesFirmwareDevice = {
  connected: boolean;
  batteryLevel: number;
  wifiConnected: boolean;
  appVersion: string;
  mtkVersion: string;
  besVersion: string;
};

export type GlassesOtaStatus = {
  session_id?: string;
  status: 'idle' | 'in_progress' | 'step_complete' | 'complete' | 'failed';
  overall_percent: number;
  step_type?: string;
  phase?: string;
  error_message?: string;
};

type FirmwareNative = {
  requestVersionInfo: () => Promise<unknown>;
  checkForOtaUpdate: () => Promise<boolean>;
  startOtaUpdate: () => Promise<unknown>;
  sendOtaQueryStatus: () => Promise<GlassesOtaStatus>;
  queryGalleryStatus: () => Promise<{ cameraBusy?: boolean }>;
};

export function getGlassesFirmwareTransport() {
  const native = loadSdk() as (BluetoothSdk & FirmwareNative) | null;
  const internal = loadInternalSdk() as (InternalBluetoothSdk & FirmwareNative) | null;
  if (!native?.checkForOtaUpdate || !native.startOtaUpdate || !internal?.sendOtaQueryStatus) {
    throw new Error('Firmware updates are unavailable in this build. Rebuild the Android app.');
  }
  return {
    check: () => native.checkForOtaUpdate(),
    start: () => native.startOtaUpdate(),
    query: () => internal.sendOtaQueryStatus(),
    refreshVersions: () => native.requestVersionInfo(),
    cameraBusy: async () => (await native.queryGalleryStatus()).cameraBusy === true,
    connectionBusy: () => activeMentraConnection !== null,
    reconnect: () =>
      ownMentraConnection(async () => {
        // After a phone-process restart there is no native controller to reconnect
        // itself. OTA may reacquire an idle saved link, but must never cancel boot,
        // replay capture settings, or use the ordinary stalled-boot reset path.
        const status = await internal.getGlassesStatus?.();
        if (isBootingConnection(status)) return false;
        if (!(await getDefaultGlassesDevice())) return false;
        pendingConnectionRecovery = null;
        await native.connectDefault({ cancelExistingConnectionAttempt: false });
        return true;
      }, true),
    subscribe: (listener: (status: GlassesOtaStatus) => void) => {
      const subscription = native.addListener('ota_status', listener);
      return () => subscription.remove();
    },
    getDevice: async (): Promise<GlassesFirmwareDevice> => {
      const status = (await internal.getGlassesStatus?.()) as Record<string, any> | undefined;
      return {
        connected:
          status?.connection?.state === 'connected' && status.connection.fullyBooted === true,
        batteryLevel: Number.isFinite(status?.batteryLevel) ? status!.batteryLevel : -1,
        wifiConnected: status?.wifi?.state === 'connected',
        appVersion: status?.appVersion || status?.buildNumber || '',
        mtkVersion: status?.mtkFirmwareVersion || '',
        besVersion: status?.besFirmwareVersion || '',
      };
    },
  };
}
