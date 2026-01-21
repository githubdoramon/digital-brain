import * as Application from 'expo-application';
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';

type DeviceRegistration = {
  expoPushToken: string;
  platform: string;
  deviceName?: string | null;
  appVersion?: string | null;
  osVersion?: string | null;
};

export async function registerForPushNotifications(): Promise<DeviceRegistration | null> {
  if (!Device.isDevice) {
    return null;
  }

  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let finalStatus = existingStatus;
  if (existingStatus !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }

  if (finalStatus !== 'granted') {
    return null;
  }

  const { data: expoPushToken } = await Notifications.getExpoPushTokenAsync();
  return {
    expoPushToken,
    platform: Device.osName?.toLowerCase() ?? 'android',
    deviceName: Device.modelName ?? null,
    appVersion: Application.nativeApplicationVersion ?? Constants.expoConfig?.version ?? null,
    osVersion: Device.osVersion ?? null,
  };
}
