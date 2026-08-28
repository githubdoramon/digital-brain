import type { CaptureKind } from './types';
import { Buffer } from 'buffer';
import * as FileSystem from 'expo-file-system/legacy';

type Subscription = { remove: () => void };
type BluetoothSdk = {
  addListener: (event: string, listener: (event: any) => void) => Subscription;
  connectDefault: () => Promise<void>;
  setGalleryModeEnabled: (enabled: boolean) => Promise<unknown>;
  setPhotoCaptureDefaults: (settings: Record<string, unknown>) => Promise<unknown>;
  setVideoRecordingDefaults: (settings: {
    width: number;
    height: number;
    fps: number;
  }) => Promise<unknown>;
  setMaxVideoRecordingDuration: (minutes: number) => Promise<unknown>;
  setHotspotState: (enabled: boolean) => Promise<unknown>;
};

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
let wifiIp: string | null = null;
let hotspot: { localIp: string; ssid: string; password: string } | null = null;
let localNetwork: LocalNetworkModule | null = null;
let scopedNetworkActive = false;
let stateListenersInitialized = false;

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
  } catch {
    sdk = null;
  }
  return sdk;
}

export function isMentraSdkAvailable(): boolean {
  return Boolean(loadSdk());
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
      // Intermediate accepted/configuring/capturing events can fire in bursts. Reconcile only
      // once the camera reports bytes are available or are being transferred; the periodic task
      // remains the backstop for a missed terminal event.
      if (['captured', 'ready_for_transfer', 'transferring'].includes(event?.status)) {
        onCaptureSignal('photo');
      }
    }),
    native.addListener('media_success', () => onCaptureSignal('photo')),
    native.addListener('video_recording_status', (event) => {
      if (event?.status === 'recording_stopped') onCaptureSignal('video');
    }),
    native.addListener('gallery_status', () => onCaptureSignal('photo')),
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
  await native.setGalleryModeEnabled(true);
  await native.setPhotoCaptureDefaults({ size: 'max', compress: 'medium', sound: true });
  await native.setVideoRecordingDefaults({ width: 1280, height: 720, fps: 30 });
  await native.setMaxVideoRecordingDuration(15);
}

export async function ensureMentraConnection(): Promise<void> {
  const native = loadSdk();
  if (!native)
    throw new Error(
      'Mentra Bluetooth SDK is not available in this build. Rebuild the Android app.',
    );
  await native.connectDefault();
  await configureCaptureDefaults();
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
