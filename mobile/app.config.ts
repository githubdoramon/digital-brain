import type { ConfigContext, ExpoConfig } from '@expo/config';
import { withAndroidManifest, withAppBuildGradle } from 'expo/config-plugins';
import { existsSync } from 'fs';
import { isAbsolute, join, resolve } from 'path';

enum AppVariant {
  Development = 'development',
  Production = 'production',
}

const resolveAppVariant = (): AppVariant => {
  const rawVariant = (process.env.APP_VARIANT ?? AppVariant.Development).trim().toLowerCase();
  return rawVariant === AppVariant.Production ? AppVariant.Production : AppVariant.Development;
};

const APP_VARIANT = resolveAppVariant();
const IS_DEV = APP_VARIANT === AppVariant.Development;

const getOptionalEnv = (name: string): string | undefined => {
  const value = process.env[name]?.trim();
  return value ? value : undefined;
};

const getUniqueIdentifier = () => {
  if (IS_DEV) {
    return 'com.appcalipse.digitalbrain.dev';
  }

  return 'com.appcalipse.digitalbrain';
};

const getAppName = () => {
  if (IS_DEV) {
    return 'Digital Brain Dev';
  }

  return 'Digital Brain';
};

const requireConfigFile = (relativePath: string, envPathName?: string) => {
  const configuredPath = envPathName ? getOptionalEnv(envPathName) : undefined;
  const resolvedPath = configuredPath
    ? isAbsolute(configuredPath)
      ? configuredPath
      : resolve(__dirname, configuredPath)
    : join(__dirname, relativePath);

  const displayPath = configuredPath ?? relativePath;
  if (!existsSync(resolvedPath)) {
    throw new Error(
      `Missing required config file: ${displayPath} for APP_VARIANT=${APP_VARIANT}.${envPathName ? ` Set ${envPathName} to an absolute or project-relative path when building locally.` : ''}`,
    );
  }

  return resolvedPath;
};

const appJson = require('./app.json');

type PluginEntry = NonNullable<ExpoConfig['plugins']>[number];

const withPlugin = (
  plugins: ExpoConfig['plugins'],
  pluginName: string,
  options?: Record<string, unknown>,
): PluginEntry[] => {
  const nextPlugins = [...(plugins ?? [])];
  const hasOptions = !!options && Object.keys(options).length > 0;
  const nextPlugin: PluginEntry = hasOptions ? [pluginName, options] : pluginName;
  const existingPluginIndex = nextPlugins.findIndex((plugin) =>
    Array.isArray(plugin) ? plugin[0] === pluginName : plugin === pluginName,
  );

  if (existingPluginIndex >= 0) {
    nextPlugins[existingPluginIndex] = nextPlugin;
  } else {
    nextPlugins.push(nextPlugin);
  }

  return nextPlugins;
};

function withSystemDebugKeystore(config: ExpoConfig) {
  return withAppBuildGradle(config, (mod) => {
    if (mod.modResults.contents) {
      mod.modResults.contents = mod.modResults.contents.replace(
        /storeFile file\('debug\.keystore'\)/,
        'storeFile file("${System.getProperty(\'user.home\')}/.android/debug.keystore")',
      );
    }
    return mod;
  });
}

/**
 * Mentra Live exposes its gallery over a short-lived local HTTP server while the
 * phone is connected to the glasses hotspot. `expo prebuild --clean` regenerates
 * AndroidManifest.xml, so keep this transport requirement in dynamic config.
 */
function withGlassesCaptureCleartext(config: ExpoConfig): ExpoConfig {
  return withAndroidManifest(config, (mod) => {
    const application = mod.modResults.manifest.application?.[0];
    if (application) {
      application.$ = {
        ...application.$,
        'android:usesCleartextTraffic': 'true',
      };
    }
    return mod;
  });
}

/**
 * Glasses alerts use Android's notification-access boundary and a temporary
 * media-playback foreground service for an incoming-call ring. Image enhancement also uses
 * a connected-device foreground service while automatic capture is enabled.
 * Keep the declarations here because Expo prebuild regenerates AndroidManifest.xml.
 */
function withGlassesAlertsAndroidManifest(config: ExpoConfig): ExpoConfig {
  return withAndroidManifest(config, (mod) => {
    const manifest = mod.modResults.manifest;
    const addPermission = (name: string) => {
      const permissions = manifest['uses-permission'] ?? [];
      if (!permissions.some((permission) => permission.$?.['android:name'] === name)) {
        permissions.push({ $: { 'android:name': name } });
      }
      manifest['uses-permission'] = permissions;
    };
    addPermission('android.permission.READ_PHONE_STATE');
    addPermission('android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK');
    addPermission('android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE');
    addPermission('android.permission.FOREGROUND_SERVICE_DATA_SYNC');

    const application = manifest.application?.[0];
    if (!application) return mod;
    const services = application.service ?? [];
    const addService = (name: string, attributes: Record<string, string>, action?: string) => {
      if (services.some((service) => service.$?.['android:name'] === name)) return;
      services.push({
        $: { 'android:name': name, ...attributes },
        ...(action ? { 'intent-filter': [{ action: [{ $: { 'android:name': action } }] }] } : {}),
      });
    };
    addService(
      'expo.modules.digitalbrainglassesalerts.GlassesAlertNotificationListenerService',
      {
        'android:label': 'Digital Brain glasses alerts',
        'android:permission': 'android.permission.BIND_NOTIFICATION_LISTENER_SERVICE',
        'android:exported': 'true',
      },
      'android.service.notification.NotificationListenerService',
    );
    addService('expo.modules.digitalbrainglassesalerts.GlassesAlertPlaybackService', {
      'android:exported': 'false',
      'android:foregroundServiceType': 'mediaPlayback',
    });
    addService('expo.modules.digitalbrainglassesalerts.GlassesImageEnhancementService', {
      'android:exported': 'false',
      'android:foregroundServiceType': 'connectedDevice',
    });
    application.service = services;

    const queries = manifest.queries?.[0] ?? {};
    const intents = queries.intent ?? [];
    const hasLauncherQuery = intents.some(
      (intent) =>
        intent.action?.some(
          (action: { $?: Record<string, string> }) =>
            action.$?.['android:name'] === 'android.intent.action.MAIN',
        ) &&
        intent.category?.some(
          (category: { $?: Record<string, string> }) =>
            category.$?.['android:name'] === 'android.intent.category.LAUNCHER',
        ),
    );
    if (!hasLauncherQuery) {
      intents.push({
        action: [{ $: { 'android:name': 'android.intent.action.MAIN' } }],
        category: [{ $: { 'android:name': 'android.intent.category.LAUNCHER' } }],
      });
    }
    queries.intent = intents;
    manifest.queries = [queries];
    return mod;
  });
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const appName = getAppName();
  const bundleId = getUniqueIdentifier();
  const androidGoogleServicesFile = requireConfigFile(
    './google-services.json',
    'GOOGLE_SERVICES_FILE',
  );
  const googleMapsApiKey = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;
  const buildTimestamp = process.env.EXPO_PUBLIC_BUILD_TIMESTAMP ?? new Date().toISOString();
  const appScheme = getOptionalEnv('APP_SCHEME');
  const expoOwner = getOptionalEnv('EXPO_ACCOUNT_OWNER');
  const easProjectId = getOptionalEnv('EXPO_PUBLIC_EAS_PROJECT_ID');
  const googleIosUrlScheme = getOptionalEnv('GOOGLE_IOS_URL_SCHEME');
  const extra = {
    ...(appJson.expo.extra ?? {}),
    ...(config.extra ?? {}),
    buildTimestamp,
    ...(easProjectId
      ? {
          eas: {
            ...((appJson.expo.extra as { eas?: Record<string, unknown> } | undefined)?.eas ?? {}),
            ...((config.extra as { eas?: Record<string, unknown> } | undefined)?.eas ?? {}),
            projectId: easProjectId,
          },
        }
      : {}),
  };

  const merged = {
    ...appJson.expo,
    ...config,
    extra,
    name: appName,
    ...(appScheme ? { scheme: appScheme } : {}),
    ...(expoOwner ? { owner: expoOwner } : {}),
    ios: {
      ...(appJson.expo.ios ?? {}),
      ...(config.ios ?? {}),
      bundleIdentifier: bundleId,
    },
    android: {
      ...(appJson.expo.android ?? {}),
      ...(config.android ?? {}),
      package: bundleId,
      googleServicesFile: androidGoogleServicesFile,
    },
  };

  const pluginsWithMaps = withPlugin(merged.plugins, 'react-native-maps', {
    ...(googleMapsApiKey ? { androidGoogleMapsApiKey: googleMapsApiKey } : {}),
    ...(googleMapsApiKey ? { iosGoogleMapsApiKey: googleMapsApiKey } : {}),
  });
  const pluginsWithGoogleSignin = withPlugin(
    pluginsWithMaps,
    '@react-native-google-signin/google-signin',
    googleIosUrlScheme ? { iosUrlScheme: googleIosUrlScheme } : undefined,
  );
  const pluginsWithAsset = withPlugin(pluginsWithGoogleSignin, 'expo-asset');
  const pluginsWithMentra = withPlugin(pluginsWithAsset, '@mentra/bluetooth-sdk');
  const pluginsWithAudioStudio = withPlugin(pluginsWithMentra, '@siteed/audio-studio', {
    enablePhoneStateHandling: false,
    enableNotifications: false,
    enableBackgroundAudio: false,
    enableDeviceDetection: false,
    iosBackgroundModes: {
      useVoIP: false,
      useAudio: false,
      useProcessing: false,
      useLocation: false,
      useExternalAccessory: false,
    },
    iosConfig: {
      microphoneUsageDescription:
        'Digital Brain uses your microphone so you can dictate chat messages with on-device Whisper transcription.',
      notificationUsageDescription:
        'Digital Brain can show recording controls while capturing audio.',
    },
  });
  const pluginsWithBuildProperties = withPlugin(pluginsWithAudioStudio, 'expo-build-properties', {
    android: {
      minSdkVersion: 28,
    },
  });

  return withGlassesAlertsAndroidManifest(
    withGlassesCaptureCleartext(
      withSystemDebugKeystore({
        ...merged,
        plugins: withPlugin(pluginsWithBuildProperties, 'expo-background-task'),
      }),
    ),
  );
};
