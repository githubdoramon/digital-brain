import { initExecutorch } from 'react-native-executorch';
import { ExpoResourceFetcher } from 'react-native-executorch-expo-resource-fetcher';

let initialized = false;

export function ensureExecutorchInitialized(): void {
  if (initialized) return;
  initExecutorch({ resourceFetcher: ExpoResourceFetcher });
  initialized = true;
}
