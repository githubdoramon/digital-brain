import Ionicons from '@expo/vector-icons/Ionicons';
import * as FileSystem from 'expo-file-system/legacy';
import { useRouter } from 'expo-router';
import React from 'react';
import { Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import {
  configureCaptureDefaults,
  getCaptureFolderUri,
  getCaptureSyncStatus,
  isMentraSdkAvailable,
  reconcileGlassesCaptures,
  retryFailedGlassesCaptures,
  setCaptureFolderUri,
  subscribeCaptureSync,
} from '@/mentraCapture';

export default function GlassesCaptureScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showSuccess, showError } = useAppNotice();
  const [status, setStatus] = React.useState(getCaptureSyncStatus());
  const [folderUri, setFolderUri] = React.useState<string | null>(null);
  const [running, setRunning] = React.useState(false);

  React.useEffect(() => {
    const unsubscribe = subscribeCaptureSync(setStatus);
    void getCaptureFolderUri().then(setFolderUri);
    return unsubscribe;
  }, []);

  const chooseFolder = async () => {
    if (Platform.OS !== 'android') return;
    const root = FileSystem.StorageAccessFramework.getUriForDirectoryInRoot('Documents');
    const result = await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync(root);
    if (result.granted && result.directoryUri) {
      const children = await FileSystem.StorageAccessFramework.readDirectoryAsync(
        result.directoryUri,
      ).catch(() => []);
      let digitalBrainUri: string | undefined = children.find((uri) =>
        decodeURIComponent(uri).endsWith('/Digital Brain'),
      );
      if (!digitalBrainUri) {
        digitalBrainUri = await FileSystem.StorageAccessFramework.makeDirectoryAsync(
          result.directoryUri,
          'Digital Brain',
        );
      }
      const captureChildren = await FileSystem.StorageAccessFramework.readDirectoryAsync(
        digitalBrainUri,
      ).catch(() => []);
      let captureQueueUri: string | undefined = captureChildren.find((uri) =>
        decodeURIComponent(uri).endsWith('/Capture Queue'),
      );
      if (!captureQueueUri) {
        captureQueueUri = await FileSystem.StorageAccessFramework.makeDirectoryAsync(
          digitalBrainUri,
          'Capture Queue',
        );
      }
      if (!captureQueueUri) throw new Error('Unable to create Capture Queue folder');
      await setCaptureFolderUri(captureQueueUri);
      setFolderUri(captureQueueUri);
      showSuccess('Capture queue folder selected.');
    }
  };

  const sync = async () => {
    setRunning(true);
    await reconcileGlassesCaptures();
    setRunning(false);
    showSuccess('Capture reconciliation finished.');
  };

  const retry = async () => {
    setRunning(true);
    await retryFailedGlassesCaptures();
    setRunning(false);
  };

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 28 },
        ]}
      >
        <Pressable style={styles.back} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color="#1a1d22" />
          <Text style={styles.backText}>Settings</Text>
        </Pressable>
        <Text style={styles.title}>Glasses capture</Text>
        <Text style={styles.subtitle}>
          Original glasses media is copied locally, acknowledged on the glasses, then uploaded to
          Immich.
        </Text>
        {Platform.OS !== 'android' ? (
          <Text style={styles.warning}>This first implementation is Android-only.</Text>
        ) : null}
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Status</Text>
          <Text style={styles.value}>
            SDK: {isMentraSdkAvailable() ? 'available' : 'native rebuild required'}
          </Text>
          <Text style={styles.value}>
            Queue: {status.pendingCount} pending · {status.failedCount} failed
          </Text>
          <Text style={styles.value}>Current: {status.currentCaptureId ?? 'idle'}</Text>
          <Text style={styles.value}>Network: {status.networkPath ?? 'idle'}</Text>
          <Text style={styles.value}>
            Last sync: {status.lastRunAt ? new Date(status.lastRunAt).toLocaleString() : 'never'}
          </Text>
          {status.lastError ? <Text style={styles.error}>{status.lastError}</Text> : null}
          <Button
            label={running ? 'Syncing…' : 'Sync now'}
            onPress={() => void sync()}
            disabled={running}
            style={styles.button}
          />
          <Button
            label="Retry failed"
            onPress={() => void retry()}
            disabled={running || status.failedCount === 0}
            variant="secondary"
            style={styles.button}
          />
        </Card>
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Capture settings</Text>
          <Text style={styles.value}>Photos: maximum supported quality, medium compression</Text>
          <Text style={styles.value}>Video: 720p at 30fps with audio, maximum 15 minutes</Text>
          <Button
            label="Apply glasses settings"
            onPress={() =>
              void configureCaptureDefaults()
                .then(() => showSuccess('Capture settings applied.'))
                .catch((error) =>
                  showError(error instanceof Error ? error.message : 'Failed to apply settings.'),
                )
            }
            variant="secondary"
            style={styles.button}
          />
        </Card>
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Local queue folder</Text>
          <Text style={styles.value}>
            {folderUri
              ? 'Custom Android Documents folder selected'
              : 'App-private fallback (select a Documents folder to make pending media visible)'}
          </Text>
          <Button
            label="Choose Documents folder"
            onPress={() => void chooseFolder()}
            variant="secondary"
            style={styles.button}
          />
        </Card>
        <Text style={styles.note}>
          Immich album: Ramon eyes capture. Local files are deleted only after the backend confirms
          the Immich asset.
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#f7f2ec' },
  content: { paddingHorizontal: 20, gap: 16 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  backText: { fontSize: 16, color: '#1a1d22' },
  title: { fontSize: 30, fontWeight: '700', color: '#1a1d22' },
  subtitle: { fontSize: 15, lineHeight: 22, color: '#5c626b' },
  warning: { color: '#8b5b17' },
  card: { padding: 16, gap: 8 },
  cardTitle: { fontSize: 18, fontWeight: '700', color: '#1a1d22' },
  value: { color: '#4e555e', lineHeight: 20 },
  error: { color: '#a83232', lineHeight: 20 },
  button: { marginTop: 4 },
  note: { color: '#5c626b', fontSize: 13, lineHeight: 19 },
});
