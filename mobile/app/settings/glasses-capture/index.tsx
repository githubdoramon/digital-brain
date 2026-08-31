import Ionicons from '@expo/vector-icons/Ionicons';
import * as FileSystem from 'expo-file-system/legacy';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Share,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
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
  getMentraConnectionStatus,
  getDefaultGlassesDevice,
  getCaptureSyncStatus,
  isMentraSdkAvailable,
  pairGlasses,
  reconcileGlassesCaptures,
  retryFailedGlassesCaptures,
  scanForGlasses,
  subscribeCaptureSync,
  clearImageEnhancementLog,
  getImageEnhancementLogFileName,
  getImageEnhancementLogInfo,
  getImageEnhancementStatus,
  initializeImageEnhancement,
  readImageEnhancementLog,
  setImageEnhancementEnabled,
  setImageEnhancementIntervalMinutes,
  subscribeImageEnhancementStatus,
  type MentraDevice,
  type MentraConnectionStatus,
} from '@/mentraCapture';
import { theme } from '@/theme';
import {
  copyToDigitalBrainStorage,
  DigitalBrainStorageFolder,
} from '@/storage/digitalBrainStorage';

async function saveAndroidDiagnostic(
  sourceUri: string,
  folder: DigitalBrainStorageFolder,
  fileName: string,
): Promise<void> {
  await copyToDigitalBrainStorage(sourceUri, folder, fileName, 'application/json');
}

export default function GlassesCaptureScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showSuccess, showError } = useAppNotice();
  const [status, setStatus] = React.useState(getCaptureSyncStatus());
  const [running, setRunning] = React.useState(false);
  const [defaultDevice, setDefaultDevice] = React.useState<MentraDevice | null>(null);
  const [connection, setConnection] = React.useState<MentraConnectionStatus>({
    hasSavedDevice: false,
    connected: false,
    fullyBooted: false,
    state: null,
  });
  const [devices, setDevices] = React.useState<MentraDevice[]>([]);
  const [scanning, setScanning] = React.useState(false);
  const [pairingDeviceId, setPairingDeviceId] = React.useState<string | null>(null);
  const [connecting, setConnecting] = React.useState(false);
  const [mentraDebugInfo, setMentraDebugInfo] = React.useState({
    exists: false,
    sizeBytes: 0,
  });
  const [imageEnhancementStatus, setImageEnhancementStatus] = React.useState(
    getImageEnhancementStatus(),
  );
  const [imageEnhancementIntervalInput, setImageEnhancementIntervalInput] = React.useState(
    String(getImageEnhancementStatus().intervalMinutes),
  );
  const [imageEnhancementLogInfo, setImageEnhancementLogInfo] = React.useState({
    exists: false,
    sizeBytes: 0,
  });

  const refreshConnection = React.useCallback(async () => {
    try {
      const [device, nextConnection] = await Promise.all([
        getDefaultGlassesDevice(),
        getMentraConnectionStatus(),
      ]);
      setDefaultDevice(device);
      setConnection(nextConnection);
    } catch {
      setConnection({ hasSavedDevice: false, connected: false, fullyBooted: false, state: null });
    }
  }, []);

  React.useEffect(() => {
    const unsubscribe = subscribeCaptureSync(setStatus);
    const unsubscribeImageEnhancement = subscribeImageEnhancementStatus((next) => {
      setImageEnhancementStatus(next);
      setImageEnhancementIntervalInput(String(next.intervalMinutes));
    });
    void refreshConnection();
    void initializeImageEnhancement();
    void getMentraDebugLogInfo().then(setMentraDebugInfo);
    void getImageEnhancementLogInfo().then(setImageEnhancementLogInfo);
    const refreshTimer = setInterval(() => {
      void getMentraDebugLogInfo().then(setMentraDebugInfo);
      void getImageEnhancementLogInfo().then(setImageEnhancementLogInfo);
      void refreshConnection();
    }, 2_000);
    return () => {
      clearInterval(refreshTimer);
      unsubscribe();
      unsubscribeImageEnhancement();
    };
  }, [refreshConnection]);

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
        await saveAndroidDiagnostic(tempFileUri, DigitalBrainStorageFolder.Exports, fileName);
        showSuccess(`Saved ${fileName} to Digital Brain / Exports.`);
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

  const updateImageEnhancementInterval = async () => {
    const parsed = Number.parseInt(imageEnhancementIntervalInput, 10);
    if (!Number.isFinite(parsed) || parsed < 1) {
      setImageEnhancementIntervalInput(String(imageEnhancementStatus.intervalMinutes));
      showError('Enter a whole number of minutes (at least 1).');
      return;
    }
    const applied = await setImageEnhancementIntervalMinutes(parsed);
    setImageEnhancementIntervalInput(String(applied));
    showSuccess(
      `Automatic glasses capture will run every ${applied} minute${applied === 1 ? '' : 's'}.`,
    );
  };

  const exportImageEnhancementLog = async () => {
    try {
      const logText = await readImageEnhancementLog();
      if (!logText.trim()) throw new Error('No image enhancement log has been recorded yet.');
      const fileName = getImageEnhancementLogFileName();
      const tempFileUri = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${fileName}`;
      await FileSystem.writeAsStringAsync(tempFileUri, logText, {
        encoding: FileSystem.EncodingType.UTF8,
      });
      if (Platform.OS === 'android') {
        await saveAndroidDiagnostic(tempFileUri, DigitalBrainStorageFolder.Exports, fileName);
        showSuccess(`Saved ${fileName} to Digital Brain / Exports.`);
      } else {
        await Share.share({ url: tempFileUri, message: logText, title: fileName });
        showSuccess('Shared image enhancement log.');
      }
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export image enhancement log.');
    }
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
      await refreshConnection();
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
      setConnection({ hasSavedDevice: false, connected: false, fullyBooted: false, state: null });
      showSuccess('Paired glasses forgotten.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not forget glasses.');
    }
  };

  const sync = async () => {
    setRunning(true);
    await reconcileGlassesCaptures();
    setRunning(false);
    const completed = getCaptureSyncStatus();
    if (completed.lastError) {
      showError(completed.lastError);
      return;
    }
    showSuccess('Capture reconciliation finished.');
  };

  const connect = async () => {
    setConnecting(true);
    try {
      const connected = await ensureMentraConnection();
      if (!connected) throw new Error('No Mentra Live is paired. Search for glasses to pair one.');
      await refreshConnection();
      await reconcileGlassesCaptures();
      const completed = getCaptureSyncStatus();
      if (completed.lastError) throw new Error(completed.lastError);
      showSuccess('Mentra Live connected and captures checked.');
    } catch (error) {
      await refreshConnection();
      showError(error instanceof Error ? error.message : 'Could not connect to Mentra Live.');
    } finally {
      setConnecting(false);
    }
  };

  const retry = async () => {
    setRunning(true);
    await retryFailedGlassesCaptures();
    setRunning(false);
  };

  const syncSummary = running
    ? 'Syncing captures now'
    : status.lastError
      ? 'Needs attention'
      : status.pendingCount > 0
        ? `${status.pendingCount} capture${status.pendingCount === 1 ? '' : 's'} waiting`
        : 'All captures are up to date';

  return (
    <View style={styles.screen}>
      <KeyboardAvoidingView
        style={styles.keyboardAvoidingView}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[
            styles.content,
            { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 28 },
          ]}
        >
          <Pressable style={styles.back} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={22} color={theme.colors.ink} />
            <Text style={styles.backText}>Settings</Text>
          </Pressable>
          <View style={styles.hero}>
            <View style={styles.heroIcon}>
              <Ionicons name="glasses-outline" size={28} color={theme.colors.teal} />
            </View>
            <View style={styles.heroCopy}>
              <Text style={styles.eyebrow}>SMART GLASSES</Text>
              <Text style={styles.title}>Smart glasses</Text>
              <Text style={styles.subtitle}>
                Capture what you see and keep it in your digital brain.
              </Text>
            </View>
          </View>
          <View style={styles.captureHint}>
            <Ionicons name="information-circle-outline" size={18} color={theme.colors.teal} />
            <Text style={styles.captureHintText}>
              Short press the right button for a photo. Hold it to start a video, then press again
              to stop. Captures stay on the glasses until they sync.
            </Text>
          </View>
          <Pressable
            style={styles.recordingsShortcut}
            onPress={() => router.push('/settings/glasses-recordings' as never)}
          >
            <View style={styles.recordingsShortcutIcon}>
              <Ionicons name="mic-outline" size={21} color={theme.colors.teal} />
            </View>
            <View style={styles.recordingsShortcutCopy}>
              <Text style={styles.recordingsShortcutTitle}>Glasses recordings</Text>
              <Text style={styles.recordingsShortcutText}>
                Record and manage audio from Mentra Live.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
          <Pressable
            style={styles.recordingsShortcut}
            onPress={() => router.push('/settings/glasses-alerts' as never)}
          >
            <View style={styles.recordingsShortcutIcon}>
              <Ionicons name="notifications-outline" size={21} color={theme.colors.teal} />
            </View>
            <View style={styles.recordingsShortcutCopy}>
              <Text style={styles.recordingsShortcutTitle}>Glasses alerts</Text>
              <Text style={styles.recordingsShortcutText}>
                Hear selected app notifications and incoming calls.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
          <Pressable
            style={styles.recordingsShortcut}
            onPress={() => router.push('/settings/glasses-capture/models' as never)}
          >
            <View style={styles.recordingsShortcutIcon}>
              <Ionicons name="hardware-chip-outline" size={21} color={theme.colors.teal} />
            </View>
            <View style={styles.recordingsShortcutCopy}>
              <Text style={styles.recordingsShortcutTitle}>Scene analysis models</Text>
              <Text style={styles.recordingsShortcutText}>
                Download or remove the local models used by automatic capture.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
          {Platform.OS !== 'android' ? (
            <Text style={styles.warning}>This first implementation is Android-only.</Text>
          ) : null}
          <Card style={styles.card}>
            <View style={styles.sectionHeader}>
              <View style={styles.sectionIcon}>
                <Ionicons name="eye-outline" size={20} color={theme.colors.teal} />
              </View>
              <View style={styles.sectionCopy}>
                <Text style={styles.cardTitle}>Automatic scene capture</Text>
                <Text style={styles.sectionSubtitle}>
                  Capture first-person scenes for local enhancement
                </Text>
              </View>
              <Switch
                value={imageEnhancementStatus.enabled}
                onValueChange={(enabled) =>
                  void setImageEnhancementEnabled(enabled).catch((error) =>
                    showError(
                      error instanceof Error ? error.message : 'Could not update capture mode.',
                    ),
                  )
                }
                trackColor={{ false: '#d8d1ca', true: theme.colors.paleTeal }}
                thumbColor={imageEnhancementStatus.enabled ? theme.colors.teal : '#fff'}
              />
            </View>
            <Text style={styles.helperText}>
              When enabled, the app asks the glasses for a new photo in the background. These photos
              never enter the normal button-capture upload queue. Photos are copied to Image
              Pipeline Temp and logs to Exports when a Digital Brain storage folder is selected;
              otherwise they remain in app storage until you choose a folder. Android shows a
              persistent notification while this mode is enabled so automatic captures can keep
              running in the background; returning to this app also triggers a catch-up capture.
              Direct Wi-Fi delivery falls back to Bluetooth automatically when the phone and glasses
              are no longer on the same network.
            </Text>
            <View style={styles.intervalRow}>
              <Text style={styles.infoTitle}>Capture every</Text>
              <TextInput
                value={imageEnhancementIntervalInput}
                onChangeText={setImageEnhancementIntervalInput}
                onBlur={() => void updateImageEnhancementInterval()}
                onSubmitEditing={() => void updateImageEnhancementInterval()}
                keyboardType="number-pad"
                returnKeyType="done"
                selectTextOnFocus
                style={styles.intervalInput}
                accessibilityLabel="Automatic capture interval in minutes"
              />
              <Text style={styles.value}>
                minute{imageEnhancementStatus.intervalMinutes === 1 ? '' : 's'}
              </Text>
            </View>
            <View style={styles.imageEnhancementStatusBox}>
              <Text style={styles.detailText}>
                {imageEnhancementStatus.running
                  ? 'Capturing and enhancing a glasses photo…'
                  : imageEnhancementStatus.enabled
                    ? `Next capture: ${imageEnhancementStatus.nextCaptureAt ? new Date(imageEnhancementStatus.nextCaptureAt).toLocaleString() : 'scheduled'}`
                    : 'Automatic capture is off'}
              </Text>
              {imageEnhancementStatus.lastCaptureAt ? (
                <Text style={styles.detailText}>
                  Last photo: {new Date(imageEnhancementStatus.lastCaptureAt).toLocaleString()} ·{' '}
                  {imageEnhancementStatus.captureCount} total
                </Text>
              ) : null}
              {imageEnhancementStatus.queuedCount > 0 ? (
                <Text style={styles.detailText}>
                  {imageEnhancementStatus.queuedCount} capture
                  {imageEnhancementStatus.queuedCount === 1 ? '' : 's'} queued for the glasses
                </Text>
              ) : null}
              {imageEnhancementStatus.failedQueueCount > 0 ? (
                <Text style={styles.error}>
                  {imageEnhancementStatus.failedQueueCount} capture
                  {imageEnhancementStatus.failedQueueCount === 1 ? '' : 's'} stopped after repeated
                  failures
                </Text>
              ) : null}
              {imageEnhancementStatus.lastError ? (
                <Text style={styles.error}>{imageEnhancementStatus.lastError}</Text>
              ) : null}
            </View>
          </Card>
          <Card style={styles.card}>
            <View style={styles.sectionHeader}>
              <View style={styles.sectionIcon}>
                <Ionicons name="bluetooth-outline" size={20} color={theme.colors.teal} />
              </View>
              <View style={styles.sectionCopy}>
                <Text style={styles.cardTitle}>Connection</Text>
                <Text style={styles.sectionSubtitle}>Connect your Mentra Live glasses</Text>
              </View>
              <View
                style={[
                  styles.statusPill,
                  connection.connected ? styles.statusPillOn : styles.statusPillOff,
                ]}
              >
                <View
                  style={[
                    styles.statusDot,
                    connection.connected ? styles.statusDotOn : styles.statusDotOff,
                  ]}
                />
                <Text style={styles.statusPillText}>
                  {connection.connected
                    ? 'Connected'
                    : defaultDevice
                      ? 'Reconnect needed'
                      : 'Not connected'}
                </Text>
              </View>
            </View>
            <View style={styles.connectionRow}>
              <Ionicons name="hardware-chip-outline" size={20} color={theme.colors.mutedInk} />
              <View style={styles.connectionCopy}>
                <Text style={styles.connectionName}>
                  {defaultDevice ? defaultDevice.name || 'Mentra Live' : 'No glasses connected'}
                </Text>
                <Text style={styles.value}>
                  {connection.connected
                    ? 'Connected and ready for captures'
                    : defaultDevice
                      ? 'Saved pairing — reconnect before capturing or syncing'
                      : 'Search nearby devices to get started'}
                </Text>
              </View>
            </View>
            <Button
              label={
                defaultDevice
                  ? connecting
                    ? 'Connecting…'
                    : 'Connect glasses'
                  : scanning
                    ? 'Searching…'
                    : 'Search for glasses'
              }
              onPress={() => void (defaultDevice ? connect() : scan())}
              disabled={scanning || connecting || running || Platform.OS !== 'android'}
              style={styles.button}
            />
            {defaultDevice ? (
              <Button
                label="Search for different glasses"
                onPress={() => void scan()}
                disabled={scanning || connecting || running || Platform.OS !== 'android'}
                variant="secondary"
                style={styles.button}
              />
            ) : null}
            {devices.map((device) => (
              <Pressable
                key={device.id}
                style={styles.deviceRow}
                onPress={() => void pair(device)}
                disabled={pairingDeviceId !== null || connecting}
              >
                <View style={styles.deviceIcon}>
                  <Ionicons name="glasses-outline" size={20} color={theme.colors.teal} />
                </View>
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
                disabled={running || pairingDeviceId !== null || connecting}
                variant="secondary"
                style={styles.button}
              />
            ) : null}
          </Card>
          <Card style={styles.card}>
            <View style={styles.sectionHeader}>
              <View style={styles.sectionIcon}>
                <Ionicons name="cloud-upload-outline" size={20} color={theme.colors.teal} />
              </View>
              <View style={styles.sectionCopy}>
                <Text style={styles.cardTitle}>Sync</Text>
                <Text style={styles.sectionSubtitle}>
                  Move captures from your glasses to Immich
                </Text>
              </View>
            </View>
            <Text style={styles.syncSummary}>{syncSummary}</Text>
            <View style={styles.metricGrid}>
              <View style={styles.metric}>
                <Text style={styles.metricValue}>{status.pendingCount}</Text>
                <Text style={styles.metricLabel}>Waiting</Text>
              </View>
              <View style={styles.metric}>
                <Text
                  style={[styles.metricValue, status.failedCount > 0 && styles.metricValueAlert]}
                >
                  {status.failedCount}
                </Text>
                <Text style={styles.metricLabel}>Failed</Text>
              </View>
              <View style={styles.metric}>
                <Text style={styles.metricValue}>{isMentraSdkAvailable() ? 'On' : 'Setup'}</Text>
                <Text style={styles.metricLabel}>SDK</Text>
              </View>
            </View>
            <View style={styles.syncDetails}>
              <Text style={styles.detailText}>
                Network: {status.networkPath ?? 'waiting for glasses'}
              </Text>
              <Text style={styles.detailText}>
                Last checked:{' '}
                {status.lastRunAt ? new Date(status.lastRunAt).toLocaleString() : 'never'}
              </Text>
            </View>
            {status.lastError ? (
              <View style={styles.errorBox}>
                <Ionicons name="warning-outline" size={18} color={theme.colors.accentDeep} />
                <Text style={styles.error}>{status.lastError}</Text>
              </View>
            ) : null}
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
            <View style={styles.sectionHeader}>
              <View style={styles.sectionIcon}>
                <Ionicons name="options-outline" size={20} color={theme.colors.teal} />
              </View>
              <View style={styles.sectionCopy}>
                <Text style={styles.cardTitle}>Capture settings</Text>
                <Text style={styles.sectionSubtitle}>Set how the glasses record</Text>
              </View>
            </View>
            <View style={styles.infoRow}>
              <Ionicons name="camera-outline" size={20} color={theme.colors.mutedInk} />
              <View style={styles.infoCopy}>
                <Text style={styles.infoTitle}>Photos</Text>
                <Text style={styles.value}>Maximum supported quality · medium compression</Text>
              </View>
            </View>
            <View style={styles.infoRow}>
              <Ionicons name="videocam-outline" size={20} color={theme.colors.mutedInk} />
              <View style={styles.infoCopy}>
                <Text style={styles.infoTitle}>Videos</Text>
                <Text style={styles.value}>720p at 30 fps with audio · up to 15 minutes</Text>
              </View>
            </View>
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
          <Card style={[styles.card, styles.troubleshootingCard]}>
            <View style={styles.sectionHeader}>
              <View style={styles.sectionIconMuted}>
                <Ionicons name="help-buoy-outline" size={20} color={theme.colors.mutedInk} />
              </View>
              <View style={styles.sectionCopy}>
                <Text style={styles.cardTitle}>Troubleshooting</Text>
                <Text style={styles.sectionSubtitle}>Only needed when something goes wrong</Text>
              </View>
            </View>
            <Text style={styles.helperText}>
              Export a redacted support log when investigating a connection or sync problem.
            </Text>
            <Text style={styles.detailText}>
              Log:{' '}
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
            <Text style={styles.detailText}>
              Image enhancement log:{' '}
              {imageEnhancementLogInfo.exists
                ? `${imageEnhancementLogInfo.sizeBytes} bytes`
                : 'not created yet'}
            </Text>
            <Button
              label="Save image enhancement log"
              onPress={() => void exportImageEnhancementLog()}
              disabled={!imageEnhancementLogInfo.exists}
              variant="secondary"
              style={styles.button}
            />
            <Button
              label="Clear image enhancement log"
              onPress={() =>
                void clearImageEnhancementLog()
                  .then(async () => {
                    setImageEnhancementLogInfo(await getImageEnhancementLogInfo());
                    showSuccess('Image enhancement log cleared.');
                  })
                  .catch((error) =>
                    showError(
                      error instanceof Error
                        ? error.message
                        : 'Could not clear image enhancement log.',
                    ),
                  )
              }
              variant="secondary"
              style={styles.button}
            />
          </Card>
          <Text style={styles.note}>
            Upload destination: Immich album “Ramon eyes capture”. Pending files are never
            discarded; local copies are removed only after the server confirms the asset.
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  keyboardAvoidingView: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 14 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  backText: { fontSize: 16, color: theme.colors.ink, fontWeight: '500' },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingTop: 4 },
  heroIcon: {
    width: 58,
    height: 58,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  heroCopy: { flex: 1, gap: 2 },
  eyebrow: { fontSize: 11, letterSpacing: 1.2, fontWeight: '700', color: theme.colors.teal },
  title: { fontSize: 30, fontWeight: '700', color: theme.colors.ink },
  subtitle: { fontSize: 15, lineHeight: 21, color: theme.colors.mutedInk },
  captureHint: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 12,
    borderRadius: theme.radius.md,
    backgroundColor: 'rgba(217,236,235,0.7)',
  },
  captureHintText: { flex: 1, color: theme.colors.teal, lineHeight: 19, fontSize: 13 },
  recordingsShortcut: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 14,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  recordingsShortcutIcon: {
    width: 38,
    height: 38,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  recordingsShortcutCopy: { flex: 1, gap: 2 },
  recordingsShortcutTitle: { color: theme.colors.ink, fontSize: 16, fontWeight: '700' },
  recordingsShortcutText: { color: theme.colors.mutedInk, fontSize: 13, lineHeight: 18 },
  warning: { color: '#8b5b17', lineHeight: 20 },
  card: { padding: 16, gap: 12 },
  troubleshootingCard: { backgroundColor: 'rgba(255,255,255,0.72)' },
  sectionHeader: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  sectionIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  sectionIconMuted: {
    width: 36,
    height: 36,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f2eee9',
  },
  sectionCopy: { flex: 1, gap: 2 },
  cardTitle: { fontSize: 18, fontWeight: '700', color: theme.colors.ink },
  sectionSubtitle: { fontSize: 13, lineHeight: 18, color: theme.colors.mutedInk },
  value: { color: theme.colors.mutedInk, lineHeight: 20 },
  connectionRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 2 },
  connectionCopy: { flex: 1, gap: 1 },
  connectionName: { color: theme.colors.ink, fontSize: 16, fontWeight: '600' },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 20,
    paddingHorizontal: 9,
    paddingVertical: 5,
  },
  statusPillOn: { backgroundColor: theme.colors.paleTeal },
  statusPillOff: { backgroundColor: '#f2eee9' },
  statusPillText: { color: theme.colors.mutedInk, fontSize: 11, fontWeight: '700' },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  statusDotOn: { backgroundColor: '#2b9872' },
  statusDotOff: { backgroundColor: '#9a938b' },
  deviceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    padding: 12,
    gap: 12,
  },
  deviceIcon: {
    width: 32,
    height: 32,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  deviceText: { flex: 1, gap: 2 },
  deviceName: { color: theme.colors.ink, fontSize: 16, fontWeight: '600' },
  deviceMeta: { color: theme.colors.mutedInk, fontSize: 13 },
  deviceAction: { color: theme.colors.accentDeep, fontWeight: '700' },
  syncSummary: { color: theme.colors.ink, fontSize: 16, fontWeight: '600' },
  metricGrid: { flexDirection: 'row', gap: 8 },
  metric: {
    flex: 1,
    borderRadius: theme.radius.md,
    backgroundColor: '#f8f5f1',
    padding: 10,
    gap: 2,
  },
  metricValue: { color: theme.colors.ink, fontSize: 18, fontWeight: '700' },
  metricValueAlert: { color: theme.colors.accentDeep },
  metricLabel: { color: theme.colors.mutedInk, fontSize: 12 },
  syncDetails: { gap: 2 },
  detailText: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 18 },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 10,
    borderRadius: theme.radius.md,
    backgroundColor: '#fdf2ef',
  },
  error: { flex: 1, color: theme.colors.accentDeep, lineHeight: 19 },
  infoRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  infoCopy: { flex: 1, gap: 1 },
  infoTitle: { color: theme.colors.ink, fontWeight: '600' },
  helperText: { color: theme.colors.mutedInk, fontSize: 13, lineHeight: 19 },
  intervalRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  intervalInput: {
    minWidth: 64,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    color: theme.colors.ink,
    textAlign: 'center',
    fontSize: 16,
    backgroundColor: '#fff',
  },
  imageEnhancementStatusBox: { gap: 3, padding: 10, borderRadius: 10, backgroundColor: '#f8f5f1' },
  button: { marginTop: 4 },
  note: { color: theme.colors.mutedInk, fontSize: 13, lineHeight: 19, paddingTop: 2 },
});
