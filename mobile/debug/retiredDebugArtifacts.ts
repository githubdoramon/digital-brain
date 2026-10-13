import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

// Upgrade cleanup for retired Settings diagnostics. User exports are left intact.
export async function clearRetiredDebugArtifacts(): Promise<void> {
  const baseDirectory = FileSystem.documentDirectory ?? FileSystem.cacheDirectory;
  const files = baseDirectory
    ? [
        `${baseDirectory}event-photo-debug-log.txt`,
        `${baseDirectory}voice-debug/`,
        `${baseDirectory}digital-brain-location-debug-log.jsonl`,
        `${baseDirectory}digital-brain-location-debug-log.jsonl.previous`,
      ]
    : [];
  await Promise.all([
    AsyncStorage.removeItem('digitalbrain.locationDebugSnapshot'),
    ...files.map((uri) => FileSystem.deleteAsync(uri, { idempotent: true })),
  ]);
}
