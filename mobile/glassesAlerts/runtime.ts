import { Platform } from 'react-native';

import GlassesAlertsNative from '@/modules/digital-brain-glasses-alerts/src';

export function isGlassesAlertsAvailable(): boolean {
  return Platform.OS === 'android' && Boolean(GlassesAlertsNative);
}

/**
 * The SDK reports the classic/BLE audio device separately from its camera BLE
 * session. Persist its name so the background notification listener can prove
 * that a sound is headed to the paired glasses, not the phone or another headset.
 */
export async function setExpectedGlassesAlertAudioDevice(deviceName: string | null): Promise<void> {
  if (!isGlassesAlertsAvailable() || !GlassesAlertsNative) return;
  await GlassesAlertsNative.setExpectedGlassesAudioDeviceName(deviceName);
}
