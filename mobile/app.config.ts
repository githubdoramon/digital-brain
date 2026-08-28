import type { ConfigContext, ExpoConfig } from '@expo/config';
import { withAppBuildGradle } from 'expo/config-plugins';
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
      notificationUsageDescription: 'Digital Brain can show recording controls while capturing audio.',
    },
  });
  const pluginsWithLiteRt = withPlugin(pluginsWithAudioStudio, 'react-native-litert-lm');
  const pluginsWithBuildProperties = withPlugin(pluginsWithLiteRt, 'expo-build-properties', {
    android: {
      minSdkVersion: 28,
    },
  });

  return withSystemDebugKeystore({
    ...merged,
    plugins: withPlugin(pluginsWithBuildProperties, 'expo-background-task'),
  });
};
