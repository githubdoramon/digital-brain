import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'digitalbrain.locationTrackingEnabled';

export async function isLocationTrackingEnabled(): Promise<boolean> {
  return (await AsyncStorage.getItem(KEY)) !== 'false';
}

export async function setLocationTrackingEnabled(enabled: boolean): Promise<void> {
  await AsyncStorage.setItem(KEY, String(enabled));
}
