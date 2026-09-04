const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

config.transformer = {
  ...config.transformer,
  babelTransformerPath: require.resolve('react-native-svg-transformer'),
};

config.resolver = {
  ...config.resolver,
  assetExts: [...new Set([...config.resolver.assetExts.filter((ext) => ext !== 'svg'), 'onnx'])],
  sourceExts: [...config.resolver.sourceExts, 'svg'],
};

module.exports = config;
