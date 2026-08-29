import Ionicons from '@expo/vector-icons/Ionicons';
import * as FileSystem from 'expo-file-system/legacy';
import { useRouter } from 'expo-router';
import React from 'react';
import { Platform, ScrollView, Share, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import {
  getMentraDebugLogFileName,
  getMentraDebugLogInfo,
  readMentraDebugLog,
  clearMentraDebugLog,
  ensureMentraConnection,
  forgetPairedGlasses,
  getDefaultGlassesDevice,
  getCaptureFolderUri,
  getCaptureSyncStatus,
  isMentraSdkAvailable,
  movePendingCapturesToFolder,
  normalizeSafDirectoryUri,
  pairGlasses,
  reconcileGlassesCaptures,
  retryFailedGlassesCaptures,
  scanForGlasses,
  setCaptureFolderUri,
  subscribeCaptureSync,
  type MentraDevice,
} from '@/mentraCapture';

export default function GlassesCaptureScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showSuccess, showError } = useAppNotice();
  const [status, setStatus] = React.useState(getCaptureSyncStatus());
  const [folderUri, setFolderUri] = React.useState<string | null>(null);
  const [running, setRunning] = React.useState(false);
  const [defaultDevice, setDefaultDevice] = React.useState<MentraDevice | null>(null);
  const [devices, setDevices] = React.useState<MentraDevice[]>([]);
  const [scanning, setScanning] = React.useState(false);
  const [pairingDeviceId, setPairingDeviceId] = React.useState<string | null>(null);
  const [mentraDebugInfo, setMentraDebugInfo] = React.useState({
    exists: false,
    sizeBytes: 0,
  });

  React.useEffect(() => {
    const unsubscribe = subscribeCaptureSync(setStatus);
    void getCaptureFolderUri().then(setFolderUri);
    void getDefaultGlassesDevice()
      .then(setDefaultDevice)
      .catch(() => setDefaultDevice(null));
    void getMentraDebugLogInfo().then(setMentraDebugInfo);
    const refreshTimer = setInterval(() => {
      void getMentraDebugLogInfo().then(setMentraDebugInfo);
    }, 2_000);
    return () => {
      clearInterval(refreshTimer);
      unsubscribe();
    };
  }, []);

  const exportMentraDiagnostics = async () => {
    try {
      const logText = await readMentraDebugLog();
      if (!logText.trim()) throw new Error('No Mentra diagnostics have been recorded yet.');
      const fileName = getMentraDebugLogFileName();
      const tempFileUri = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${fileName}`;
      await FileSystem.writeAsStringAsync(tempFileUri, logText, {
        encoding: FileSystem.EncodingType.UTF8,
      });
      if (Platform.OS === 'android') {
        const initialUri = FileSystem.StorageAccessFramework.getUriForDirectoryInRoot('Download');
        const permission =
          await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
        if (!permission.granted || !permission.directoryUri) {
          throw new Error('Downloads access not granted.');
        }
        const targetUri = await FileSystem.StorageAccessFramework.createFileAsync(
          permission.directoryUri,
          fileName.replace(/\.jsonl$/i, ''),
          'application/json',
        );
        const base64Content = await FileSystem.readAsStringAsync(tempFileUri, {
          encoding: FileSystem.EncodingType.Base64,
        });
        await FileSystem.writeAsStringAsync(targetUri, base64Content, {
          encoding: FileSystem.EncodingType.Base64,
        });
        showSuccess(`Saved Mentra diagnostics to Downloads as ${fileName}.`);
      } else {
        await Share.share({ url: tempFileUri, message: logText, title: fileName });
        showSuccess('Shared Mentra diagnostics.');
      }
      setMentraDebugInfo(await getMentraDebugLogInfo());
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export Mentra diagnostics.');
    }
  };

  const clearMentraDiagnostics = async () => {
    await clearMentraDebugLog();
    setMentraDebugInfo(await getMentraDebugLogInfo());
    showSuccess('Mentra diagnostics cleared.');
  };

  const scan = async () => {
    setScanning(true);
    setDevices([]);
    try {
      const found = await scanForGlasses(setDevices);
      setDevices(found);
      if (found.length === 0) {
        showError(
          'No Mentra Live glasses found. Make sure Bluetooth is on and the glasses are awake. If they are already paired in Android settings, no separate pairing mode is required.',
        );
      }
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not scan for glasses.');
    } finally {
      setScanning(false);
    }
  };

  const pair = async (device: MentraDevice) => {
    setPairingDeviceId(device.id);
    try {
      await pairGlasses(device);
      setDefaultDevice(device);
      setDevices([]);
      showSuccess(`${device.name || 'Mentra Live'} paired and ready.`);
      await reconcileGlassesCaptures();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not pair glasses.');
    } finally {
      setPairingDeviceId(null);
    }
  };

  const forget = async () => {
    try {
      await forgetPairedGlasses();
      setDefaultDevice(null);
      showSuccess('Paired glasses forgotten.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not forget glasses.');
    }
  };

  const chooseFolder = async () => {
    if (Platform.OS !== 'android') return;
    const root = FileSystem.StorageAccessFramework.getUriForDirectoryInRoot('Documents');
    const result = await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync(root);
    if (result.granted && result.directoryUri) {
      // Keep the URI returned by ACTION_OPEN_DOCUMENT_TREE as the permission
      // grant. Do not create nested children and synthesize a new tree URI: the
      // provider may grant access to the selected folder only, and Android can
      // reject a reconstructed child URI later during sync. The picker opens at
      // Documents; select (or create) Digital Brain/Capture Queue itself.
      const selectedDirectoryUri = normalizeSafDirectoryUri(result.directoryUri);
      await setCaptureFolderUri(selectedDirectoryUri);
      setFolderUri(selectedDirectoryUri);
      const migration = await movePendingCapturesToFolder(selectedDirectoryUri);
      if (migration.failed > 0) {
        showError(
          `Capture queue folder selected. ${migration.moved} existing capture(s) made visible; ${migration.failed} could not be copied.`,
        );
      } else if (migration.moved > 0) {
        showSuccess(
          `Capture queue folder selected. ${migration.moved} existing capture(s) are now visible there.`,
        );
      } else {
        showSuccess('Capture queue folder selected.');
      }
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
        <Text style={styles.subtitle}>
          Use the right action button on the glasses: short press takes a photo; long press starts a
          video; press again to stop it. Gallery mode saves captures on the glasses until sync.
        </Text>
        {Platform.OS !== 'android' ? (
          <Text style={styles.warning}>This first implementation is Android-only.</Text>
        ) : null}
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Glasses connection</Text>
          <Text style={styles.value}>
            {defaultDevice
              ? `Paired: ${defaultDevice.name || 'Mentra Live'}`
              : 'No glasses paired with this phone.'}
          </Text>
          <Button
            label={scanning ? 'Searching…' : 'Search for glasses'}
            onPress={() => void scan()}
            disabled={scanning || running || Platform.OS !== 'android'}
            style={styles.button}
          />
          {devices.map((device) => (
            <Pressable
              key={device.id}
              style={styles.deviceRow}
              onPress={() => void pair(device)}
              disabled={pairingDeviceId !== null}
            >
              <View style={styles.deviceText}>
                <Text style={styles.deviceName}>{device.name || 'Mentra Live'}</Text>
                <Text style={styles.deviceMeta}>{device.model}</Text>
              </View>
              <Text style={styles.deviceAction}>
                {pairingDeviceId === device.id ? 'Pairing…' : 'Pair'}
              </Text>
            </Pressable>
          ))}
          {defaultDevice ? (
            <Button
              label="Forget paired glasses"
              onPress={() => void forget()}
              disabled={running || pairingDeviceId !== null}
              variant="secondary"
              style={styles.button}
            />
          ) : null}
        </Card>
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
              void (async () => {
                try {
                  if (!(await getDefaultGlassesDevice())) {
                    throw new Error('Pair a Mentra Live before applying capture settings.');
                  }
                  const connected = await ensureMentraConnection();
                  if (!connected)
                    throw new Error('Pair a Mentra Live before applying capture settings.');
                  showSuccess('Capture settings applied.');
                } catch (error) {
                  showError(error instanceof Error ? error.message : 'Failed to apply settings.');
                }
              })()
            }
            variant="secondary"
            style={styles.button}
          />
        </Card>
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Local queue folder</Text>
          <Text style={styles.value}>
            {folderUri
              ? 'Custom Android folder selected (sync falls back privately if Android access expires)'
              : 'App-private fallback (select the Capture Queue folder to make pending media visible)'}
          </Text>
          <Button
            label="Choose Capture Queue folder"
            onPress={() => void chooseFolder()}
            variant="secondary"
            style={styles.button}
          />
        </Card>
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Mentra diagnostics</Text>
          <Text style={styles.value}>
            Captures connection, gallery-mode, physical-button, camera, and sync events. Media
            bytes, file paths, URLs, and credentials are redacted.
          </Text>
          <Text style={styles.value}>
            Log file:{' '}
            {mentraDebugInfo.exists ? `${mentraDebugInfo.sizeBytes} bytes` : 'not created yet'}
          </Text>
          <Button
            label="Download diagnostics"
            onPress={() => void exportMentraDiagnostics()}
            disabled={!mentraDebugInfo.exists}
            variant="secondary"
            style={styles.button}
          />
          <Button
            label="Clear diagnostics"
            onPress={() => void clearMentraDiagnostics()}
            disabled={!mentraDebugInfo.exists}
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
  deviceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderWidth: 1,
    borderColor: '#ded8d1',
    borderRadius: 12,
    padding: 12,
    gap: 12,
  },
  deviceText: { flex: 1, gap: 2 },
  deviceName: { color: '#1a1d22', fontSize: 16, fontWeight: '600' },
  deviceMeta: { color: '#6a7078', fontSize: 13 },
  deviceAction: { color: '#d8584e', fontWeight: '700' },
  error: { color: '#a83232', lineHeight: 20 },
  button: { marginTop: 4 },
  note: { color: '#5c626b', fontSize: 13, lineHeight: 19 },
});
