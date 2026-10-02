import { PermissionsAndroid, Platform } from 'react-native';

import { API_BASE_URL } from '../api/client';
import RuntimeNative from '../modules/digital-brain-runtime/src';

export async function syncGlassesSignedIn(signedIn: boolean): Promise<void> {
  if (Platform.OS === 'android' && RuntimeNative?.setGlassesSignedIn) {
    const clientId = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
    if (signedIn && clientId)
      await RuntimeNative.configureRuntimeLocationUploader(API_BASE_URL, clientId);
    await RuntimeNative.setGlassesSignedIn(signedIn);
  }
}

export async function requestGlassesPermissions(): Promise<void> {
  if (Platform.OS !== 'android')
    throw new Error('Glasses are available on Android for this stage.');
  const required =
    Number(Platform.Version) >= 31
      ? [
          PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN,
          PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
        ]
      : [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
  const results = await PermissionsAndroid.requestMultiple(required);
  if (required.some((permission) => results[permission] !== PermissionsAndroid.RESULTS.GRANTED)) {
    throw new Error('Allow Nearby devices access in Android Settings to connect your glasses.');
  }
  await requestGlassesWifiPermission();
  if (Number(Platform.Version) >= 33) {
    await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
  }
}

export function glassesNative() {
  if (Platform.OS !== 'android' || !RuntimeNative?.getGlassesStatus) {
    throw new Error('Install a new Android native build to enable Glasses.');
  }
  return RuntimeNative;
}

export async function requestGlassesWifiPermission(): Promise<void> {
  const permission =
    Number(Platform.Version) >= 33
      ? PermissionsAndroid.PERMISSIONS.NEARBY_WIFI_DEVICES
      : PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION;
  const result = await PermissionsAndroid.request(permission);
  if (result !== PermissionsAndroid.RESULTS.GRANTED)
    throw new Error('Allow Wi-Fi access to transfer originals from glasses.');
}
