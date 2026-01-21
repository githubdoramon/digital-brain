import type { ConfigContext, ExpoConfig } from '@expo/config';
import { withAppBuildGradle } from '@expo/config-plugins';

const appJson = require('./app.json');

function withSystemDebugKeystore(config: ExpoConfig) {
  return withAppBuildGradle(config, (mod) => {
    if (mod.modResults.contents) {
      mod.modResults.contents = mod.modResults.contents.replace(
        /storeFile file\('debug\.keystore'\)/,
        "storeFile file(\"${System.getProperty('user.home')}/.android/debug.keystore\")",
      );
    }
    return mod;
  });
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const merged = {
    ...appJson.expo,
    ...config,
  };

  return withSystemDebugKeystore({
    ...merged,
    plugins: [...(merged.plugins ?? [])],
  });
};
