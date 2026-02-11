import type { ConfigContext, ExpoConfig } from '@expo/config';
import { withAppBuildGradle } from '@expo/config-plugins';
import { existsSync } from 'fs';
import { join } from 'path';

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

const requireConfigFile = (relativePath: string) => {
  const absolutePath = join(__dirname, relativePath);
  if (!existsSync(absolutePath)) {
    throw new Error(
      `Missing required config file: ${relativePath} for APP_VARIANT=${APP_VARIANT}.`,
    );
  }

  return relativePath;
};

const appJson = require('./app.json');

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
  const androidGoogleServicesFile = requireConfigFile('./google-services.json');

  const merged = {
    ...appJson.expo,
    ...config,
    name: appName,
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

  return withSystemDebugKeystore({
    ...merged,
    plugins: [...(merged.plugins ?? [])],
  });
};
