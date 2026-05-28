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
    console.warn('[notifications] Push registration skipped: physical device required');
    return null;
  }

  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let finalStatus = existingStatus;
  console.info('[notifications] Existing push permission status', { status: existingStatus });
  if (existingStatus !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
    console.info('[notifications] Push permission request finished', { status });
  }

  if (finalStatus !== 'granted') {
    console.warn('[notifications] Push registration skipped: permission not granted', {
      status: finalStatus,
    });
    return null;
  }

  const projectId =
    process.env.EXPO_PUBLIC_EAS_PROJECT_ID ??
    Constants.easConfig?.projectId ??
    Constants.expoConfig?.extra?.eas?.projectId ??
    undefined;
  console.info('[notifications] Requesting Expo push token', {
    hasProjectId: Boolean(projectId),
    projectIdSource: process.env.EXPO_PUBLIC_EAS_PROJECT_ID
      ? 'env'
      : Constants.easConfig?.projectId
        ? 'easConfig'
        : Constants.expoConfig?.extra?.eas?.projectId
          ? 'expoConfig'
          : 'missing',
  });
  const { data: expoPushToken } = await Notifications.getExpoPushTokenAsync(
    projectId ? { projectId } : undefined,
  );
  console.info('[notifications] Expo push token received', {
    tokenPrefix: expoPushToken.slice(0, 18),
  });
  return {
    expoPushToken,
    platform: Device.osName?.toLowerCase() ?? 'android',
    deviceName: Device.modelName ?? null,
    appVersion: Application.nativeApplicationVersion ?? Constants.expoConfig?.version ?? null,
    osVersion: Device.osVersion ?? null,
  };
}

export async function getDeviceRegistrationIfGranted(): Promise<DeviceRegistration | null> {
  if (!Device.isDevice) {
    console.warn('[notifications] Push registration check skipped: physical device required');
    return null;
  }

  const { status } = await Notifications.getPermissionsAsync();
  console.info('[notifications] Push permission check for existing registration', { status });
  if (status !== 'granted') {
    return null;
  }

  const projectId =
    process.env.EXPO_PUBLIC_EAS_PROJECT_ID ??
    Constants.easConfig?.projectId ??
    Constants.expoConfig?.extra?.eas?.projectId ??
    undefined;
  console.info('[notifications] Refreshing Expo push token for granted device', {
    hasProjectId: Boolean(projectId),
    projectIdSource: process.env.EXPO_PUBLIC_EAS_PROJECT_ID
      ? 'env'
      : Constants.easConfig?.projectId
        ? 'easConfig'
        : Constants.expoConfig?.extra?.eas?.projectId
          ? 'expoConfig'
          : 'missing',
  });
  const { data: expoPushToken } = await Notifications.getExpoPushTokenAsync(
    projectId ? { projectId } : undefined,
  );
  console.info('[notifications] Existing Expo push token refreshed', {
    tokenPrefix: expoPushToken.slice(0, 18),
  });
  return {
    expoPushToken,
    platform: Device.osName?.toLowerCase() ?? 'android',
    deviceName: Device.modelName ?? null,
    appVersion: Application.nativeApplicationVersion ?? Constants.expoConfig?.version ?? null,
    osVersion: Device.osVersion ?? null,
  };
}
