import type { ConfigContext, ExpoConfig } from '@expo/config';
import { withAppBuildGradle } from '@expo/config-plugins';

const IS_DEV = process.env.APP_VARIANT === 'development';

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
    },
  };

  return withSystemDebugKeystore({
    ...merged,
    plugins: [...(merged.plugins ?? [])],
  });
};
