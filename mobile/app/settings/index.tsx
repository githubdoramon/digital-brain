import Ionicons from '@expo/vector-icons/Ionicons';
import * as Application from 'expo-application';
import Constants from 'expo-constants';
import * as FileSystem from 'expo-file-system/legacy';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import { Animated, Platform, Share, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import {
  getBackgroundLocationDebugStatus,
  type BackgroundLocationDebugStatus,
} from '@/location/backgroundLocation';
import {
  clearEventPhotoDebugLog,
  getEventPhotoDebugLogFileName,
  getEventPhotoDebugLogInfo,
  readEventPhotoDebugLog,
} from '@/debug/eventPhotoDebugLog';
import {
  clearVoiceTranscriptionDebugArtifacts,
  getVoiceTranscriptionDebugAudioFileName,
  getVoiceTranscriptionDebugInfo,
  getVoiceTranscriptionDebugLogFileName,
  readVoiceTranscriptionDebugLog,
} from '@/debug/voiceTranscriptionDebug';
import {
  getLocationDebugSnapshot,
  buildLocationDebugLogText,
  getLocationDebugLogInfo,
  hydrateLocationDebugSnapshot,
  isBackgroundRelevantLocationEvent,
  readLocationDebugLogText,
  subscribeLocationDebug,
  type LocationDebugEvent,
  type LocationDebugSnapshot,
} from '@/location/debugState';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

const { StorageAccessFramework } = FileSystem;

function formatBuildTimestamp(value: string | null | undefined): string {
  if (!value) {
    return 'Unknown';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function formatDebugPayload(payload: unknown): string {
  if (!payload) {
    return 'none';
  }
  try {
    return JSON.stringify(payload);
  } catch {
    return 'unserializable payload';
  }
}

function getPayloadString(payload: Record<string, unknown> | undefined, key: string): string {
  const value = payload?.[key];
  return typeof value === 'string' && value ? value : 'none';
}

function getPayloadNumber(payload: Record<string, unknown> | undefined, key: string): string {
  const value = payload?.[key];
  return typeof value === 'number' ? String(value) : 'none';
}

function getPayloadBoolean(payload: Record<string, unknown> | undefined, key: string): string {
  const value = payload?.[key];
  return typeof value === 'boolean' ? String(value) : 'none';
}

function LocationDebugEventRow({ event }: { event: LocationDebugEvent }) {
  return (
    <View style={styles.debugEventRow}>
      <Text style={styles.debugEventTitle}>{event.eventName}</Text>
      <Text style={styles.debugEventMeta}>{formatBuildTimestamp(event.at)}</Text>
      <Text style={styles.debugEventMeta}>
        Successes before failure: {event.successCountSincePreviousFailure ?? 0}
      </Text>
      <Text style={styles.debugEventMeta}>
        Captured at: {formatBuildTimestamp(getPayloadString(event.payload, 'captured_at'))}
      </Text>
      <Text style={styles.debugEventMeta}>
        Batch window:{' '}
        {formatBuildTimestamp(getPayloadString(event.payload, 'batch_first_captured_at'))} -{' '}
        {formatBuildTimestamp(getPayloadString(event.payload, 'batch_last_captured_at'))}
      </Text>
      <Text style={styles.debugEventMeta}>
        Request URL: {getPayloadString(event.payload, 'request_url')}
      </Text>
      <Text style={styles.debugEventMeta}>Status: {getPayloadNumber(event.payload, 'status')}</Text>
      <Text style={styles.debugEventMeta}>
        Content-Type: {getPayloadString(event.payload, 'content_type')}
      </Text>
      <Text style={styles.debugEventMeta}>
        App state: {getPayloadString(event.payload, 'app_state')}
      </Text>
      <Text style={styles.debugEventMeta}>
        Token present: {getPayloadBoolean(event.payload, 'token_present')}
      </Text>
      <Text style={styles.debugEventMeta}>
        Token fingerprint: {getPayloadString(event.payload, 'token_fingerprint')}
      </Text>
      <Text style={styles.debugEventMeta}>
        Token expires at:{' '}
        {formatBuildTimestamp(getPayloadString(event.payload, 'token_expires_at'))}
      </Text>
      <Text style={styles.debugEventMeta}>
        Token expires in: {getPayloadNumber(event.payload, 'token_expires_in_seconds')}
      </Text>
      <Text style={styles.debugEventMeta}>
        Token expired: {getPayloadBoolean(event.payload, 'token_is_expired')}
      </Text>
      <Text style={styles.debugEventMeta}>Message: {event.message ?? 'none'}</Text>
      <Text style={styles.debugEventMeta}>Error: {event.error ?? 'none'}</Text>
      <Text style={styles.debugEventPayload}>Payload: {formatDebugPayload(event.payload)}</Text>
    </View>
  );
}

function getRelevantBackgroundEvents(snapshot: LocationDebugSnapshot): LocationDebugEvent[] {
  return (snapshot.eventLog ?? []).filter(isBackgroundRelevantLocationEvent);
}

export default function SettingsScreen() {
  const router = useRouter();
  const { signOut } = useAuth();
  const { showSuccess, showError } = useAppNotice();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const [locationDebug, setLocationDebug] = React.useState<LocationDebugSnapshot>(() =>
    getLocationDebugSnapshot(),
  );
  const [backgroundStatus, setBackgroundStatus] =
    React.useState<BackgroundLocationDebugStatus | null>(null);
  const [isRefreshingLocationDebug, setIsRefreshingLocationDebug] = React.useState(false);
  const [isExportingLocationDebug, setIsExportingLocationDebug] = React.useState(false);
  const [locationDebugLogInfo, setLocationDebugLogInfo] = React.useState<{
    exists: boolean;
    sizeBytes: number;
  }>({
    exists: false,
    sizeBytes: 0,
  });
  const [eventPhotoDebugInfo, setEventPhotoDebugInfo] = React.useState<{
    exists: boolean;
    sizeBytes: number;
  }>({
    exists: false,
    sizeBytes: 0,
  });
  const [isExportingEventPhotoDebug, setIsExportingEventPhotoDebug] = React.useState(false);
  const [voiceDebugInfo, setVoiceDebugInfo] = React.useState<{
    logExists: boolean;
    logSizeBytes: number;
    audioExists: boolean;
    audioSizeBytes: number;
    audioUri: string | null;
  }>({
    logExists: false,
    logSizeBytes: 0,
    audioExists: false,
    audioSizeBytes: 0,
    audioUri: null,
  });
  const [isExportingVoiceDebugLog, setIsExportingVoiceDebugLog] = React.useState(false);
  const [isExportingVoiceDebugAudio, setIsExportingVoiceDebugAudio] = React.useState(false);
  const appVersion =
    Application.nativeApplicationVersion ?? Constants.expoConfig?.version ?? 'Unknown';
  const buildNumber = Application.nativeBuildVersion ?? 'Unknown';
  const buildTimestamp = formatBuildTimestamp(
    (Constants.expoConfig?.extra as { buildTimestamp?: string } | undefined)?.buildTimestamp,
  );

  const refreshLocationDebug = React.useCallback(async () => {
    setIsRefreshingLocationDebug(true);
    try {
      const status = await getBackgroundLocationDebugStatus();
      setBackgroundStatus(status);
      setLocationDebug(await hydrateLocationDebugSnapshot());
      setLocationDebugLogInfo(await getLocationDebugLogInfo());
      setEventPhotoDebugInfo(await getEventPhotoDebugLogInfo());
      setVoiceDebugInfo(await getVoiceTranscriptionDebugInfo());
    } finally {
      setIsRefreshingLocationDebug(false);
    }
  }, []);

  React.useEffect(() => {
    const unsubscribe = subscribeLocationDebug((next) => {
      setLocationDebug(next);
    });
    void refreshLocationDebug();
    return unsubscribe;
  }, [refreshLocationDebug]);

  const backgroundEvents = React.useMemo(
    () => getRelevantBackgroundEvents(locationDebug),
    [locationDebug],
  );
  const lastBackgroundEvent = backgroundEvents[0] ?? null;
  const backgroundFailures = React.useMemo(
    () => (locationDebug.recentFailures ?? []).filter(isBackgroundRelevantLocationEvent),
    [locationDebug],
  );

  const exportLocationDebug = React.useCallback(async () => {
    setIsExportingLocationDebug(true);
    try {
      await getBackgroundLocationDebugStatus();
      const currentSnapshot = await hydrateLocationDebugSnapshot();
      const logText =
        (await readLocationDebugLogText({ backgroundOnly: true })) ||
        buildLocationDebugLogText(currentSnapshot, { backgroundOnly: true });
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const fileName = `digital-brain-background-location-debug-${timestamp}.txt`;
      const tempFileUri = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${fileName}`;
      await FileSystem.writeAsStringAsync(tempFileUri, logText, {
        encoding: FileSystem.EncodingType.UTF8,
      });

      if (Platform.OS === 'android') {
        const initialUri = StorageAccessFramework.getUriForDirectoryInRoot('Download');
        const permission =
          await StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
        if (!permission.granted || !permission.directoryUri) {
          throw new Error('Downloads access not granted.');
        }

        const targetUri = await StorageAccessFramework.createFileAsync(
          permission.directoryUri,
          fileName.replace(/\.txt$/i, ''),
          'text/plain',
        );
        const base64Content = await FileSystem.readAsStringAsync(tempFileUri, {
          encoding: FileSystem.EncodingType.Base64,
        });
        await FileSystem.writeAsStringAsync(targetUri, base64Content, {
          encoding: FileSystem.EncodingType.Base64,
        });
        setLocationDebugLogInfo(await getLocationDebugLogInfo());
        showSuccess(`Saved to Downloads as ${fileName}.`);
        return;
      }

      await Share.share({
        url: tempFileUri,
        message: logText,
        title: fileName,
      });
      setLocationDebugLogInfo(await getLocationDebugLogInfo());
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to export background location debug log.';
      showError(message);
    } finally {
      setIsExportingLocationDebug(false);
    }
  }, [showError, showSuccess]);

  const exportEventPhotoDebug = React.useCallback(async () => {
    setIsExportingEventPhotoDebug(true);
    try {
      const logText = await readEventPhotoDebugLog();
      if (!logText.trim()) {
        throw new Error('No event photo debug log is available yet.');
      }

      const fileName = getEventPhotoDebugLogFileName();
      const tempFileUri = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${fileName}`;
      await FileSystem.writeAsStringAsync(tempFileUri, logText, {
        encoding: FileSystem.EncodingType.UTF8,
      });

      let exported = false;
      if (Platform.OS === 'android') {
        const initialUri = StorageAccessFramework.getUriForDirectoryInRoot('Download');
        const permission =
          await StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
        if (!permission.granted || !permission.directoryUri) {
          throw new Error('Downloads access not granted.');
        }

        const targetUri = await StorageAccessFramework.createFileAsync(
          permission.directoryUri,
          fileName.replace(/\.txt$/i, ''),
          'text/plain',
        );
        const base64Content = await FileSystem.readAsStringAsync(tempFileUri, {
          encoding: FileSystem.EncodingType.Base64,
        });
        await FileSystem.writeAsStringAsync(targetUri, base64Content, {
          encoding: FileSystem.EncodingType.Base64,
        });
        exported = true;
        showSuccess(`Saved event photo debug to Downloads as ${fileName}.`);
      } else {
        const shareResult = await Share.share({
          url: tempFileUri,
          message: logText,
          title: fileName,
        });
        exported = shareResult.action !== Share.dismissedAction;
        if (exported) {
          showSuccess('Shared event photo debug log.');
        }
      }

      if (exported) {
        await clearEventPhotoDebugLog();
        setEventPhotoDebugInfo(await getEventPhotoDebugLogInfo());
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to export event photo debug log.';
      showError(message);
    } finally {
      setIsExportingEventPhotoDebug(false);
    }
  }, [showError, showSuccess]);

  const exportVoiceDebugLog = React.useCallback(async () => {
    setIsExportingVoiceDebugLog(true);
    try {
      const logText = await readVoiceTranscriptionDebugLog();
      if (!logText.trim()) {
        throw new Error('No voice transcription debug log is available yet.');
      }

      const fileName = getVoiceTranscriptionDebugLogFileName();
      const tempFileUri = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${fileName}`;
      await FileSystem.writeAsStringAsync(tempFileUri, logText, {
        encoding: FileSystem.EncodingType.UTF8,
      });

      let exported = false;
      if (Platform.OS === 'android') {
        const initialUri = StorageAccessFramework.getUriForDirectoryInRoot('Download');
        const permission =
          await StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
        if (!permission.granted || !permission.directoryUri) {
          throw new Error('Downloads access not granted.');
        }

        const targetUri = await StorageAccessFramework.createFileAsync(
          permission.directoryUri,
          fileName.replace(/\.txt$/i, ''),
          'text/plain',
        );
        const base64Content = await FileSystem.readAsStringAsync(tempFileUri, {
          encoding: FileSystem.EncodingType.Base64,
        });
        await FileSystem.writeAsStringAsync(targetUri, base64Content, {
          encoding: FileSystem.EncodingType.Base64,
        });
        exported = true;
        showSuccess(`Saved voice debug log to Downloads as ${fileName}.`);
      } else {
        const shareResult = await Share.share({
          url: tempFileUri,
          message: logText,
          title: fileName,
        });
        exported = shareResult.action !== Share.dismissedAction;
        if (exported) {
          showSuccess('Shared voice transcription debug log.');
        }
      }

      if (exported) {
        setVoiceDebugInfo(await getVoiceTranscriptionDebugInfo());
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to export voice transcription debug log.';
      showError(message);
    } finally {
      setIsExportingVoiceDebugLog(false);
    }
  }, [showError, showSuccess]);

  const exportVoiceDebugAudio = React.useCallback(async () => {
    setIsExportingVoiceDebugAudio(true);
    try {
      const current = await getVoiceTranscriptionDebugInfo();
      if (!current.audioExists || !current.audioUri) {
        throw new Error('No recorded voice sample is available yet.');
      }

      const fileName = getVoiceTranscriptionDebugAudioFileName();
      const tempFileUri = `${FileSystem.cacheDirectory ?? FileSystem.documentDirectory}${fileName}`;
      await FileSystem.copyAsync({ from: current.audioUri, to: tempFileUri });

      if (Platform.OS === 'android') {
        const initialUri = StorageAccessFramework.getUriForDirectoryInRoot('Download');
        const permission =
          await StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
        if (!permission.granted || !permission.directoryUri) {
          throw new Error('Downloads access not granted.');
        }

        const targetUri = await StorageAccessFramework.createFileAsync(
          permission.directoryUri,
          fileName.replace(/\.m4a$/i, ''),
          'audio/mp4',
        );
        const base64Content = await FileSystem.readAsStringAsync(tempFileUri, {
          encoding: FileSystem.EncodingType.Base64,
        });
        await FileSystem.writeAsStringAsync(targetUri, base64Content, {
          encoding: FileSystem.EncodingType.Base64,
        });
        showSuccess(`Saved voice sample to Downloads as ${fileName}.`);
      } else {
        const shareResult = await Share.share({
          url: tempFileUri,
          title: fileName,
        });

        if (shareResult.action !== Share.dismissedAction) {
          showSuccess('Shared latest voice transcription sample.');
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to export voice sample.';
      showError(message);
    } finally {
      setIsExportingVoiceDebugAudio(false);
    }
  }, [showError, showSuccess]);

  const clearVoiceDebug = React.useCallback(async () => {
    try {
      await clearVoiceTranscriptionDebugArtifacts();
      setVoiceDebugInfo(await getVoiceTranscriptionDebugInfo());
      showSuccess('Cleared voice transcription debug artifacts.');
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : 'Failed to clear voice transcription debug artifacts.';
      showError(message);
    }
  }, [showError, showSuccess]);

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <Animated.ScrollView
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        contentContainerStyle={[
          styles.content,
          {
            paddingTop:
              insets.top +
              COLLAPSING_TOP_BAR_HEIGHT +
              COLLAPSING_CONTENT_TOP_PADDING +
              COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
            paddingBottom: insets.bottom + 24,
          },
        ]}
      >
        <Card style={[styles.card, styles.navCard]}>
          <Pressable
            style={styles.navRow}
            onPress={() => router.push('/settings/image-understanding' as never)}
          >
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>Image understanding POC</Text>
              <Text style={styles.rowSubtitle}>
                Compare fast deterministic vision with on-device Gemma 4.
              </Text>
            </View>
            <Ionicons name="scan-outline" size={20} color={theme.colors.mutedInk} />
          </Pressable>
          <Pressable
            style={styles.navRow}
            onPress={() => router.push('/settings/glasses-capture' as never)}
          >
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>Glasses capture</Text>
              <Text style={styles.rowSubtitle}>Sync photos and videos to Immich</Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </Card>

        <Card style={[styles.card, styles.navCard]}>
          <Pressable
            style={styles.navRow}
            onPress={() => router.push('/settings/proposed-events' as never)}
          >
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>Proposed events</Text>
              <Text style={styles.rowSubtitle}>
                Review places where your Brain found gaps in the day.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </Card>

        <Card style={[styles.card, styles.navCard]}>
          <Pressable style={styles.navRow} onPress={() => router.push('/settings/notifications')}>
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>Notifications</Text>
              <Text style={styles.rowSubtitle}>Choose alerts per type and delivery channel.</Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </Card>

        <Card style={[styles.card, styles.navCard]}>
          <Pressable style={styles.navRow} onPress={() => router.push('/settings/about-me')}>
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>About me</Text>
              <Text style={styles.rowSubtitle}>
                View and manage what your Brain has learned about you.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </Card>

        <Card style={[styles.card, styles.navCard]}>
          <Pressable style={styles.navRow} onPress={() => router.push('/settings/news-topics')}>
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>News topics</Text>
              <Text style={styles.rowSubtitle}>
                Manage tracked topics for your daily briefing news feed.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </Card>

        <Card style={[styles.card, styles.versionCard]}>
          <Text style={styles.versionLabel}>App version</Text>
          <Text style={styles.versionValue}>{`${appVersion} (${buildNumber})`}</Text>
          <Text style={styles.versionLabel}>Build timestamp</Text>
          <Text style={styles.versionValue}>{buildTimestamp}</Text>
        </Card>

        <Card style={[styles.card, styles.versionCard]}>
          <Text style={styles.versionLabel}>Voice transcription debug</Text>
          <Text style={styles.versionValue}>
            Log file present: {voiceDebugInfo.logExists ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Log file size: {voiceDebugInfo.logSizeBytes} bytes
          </Text>
          <Text style={styles.versionValue}>
            Audio sample present: {voiceDebugInfo.audioExists ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Audio sample size: {voiceDebugInfo.audioSizeBytes} bytes
          </Text>
          <Button
            label={isExportingVoiceDebugLog ? 'Exporting...' : 'Download voice debug log'}
            onPress={() => {
              void exportVoiceDebugLog();
            }}
            variant="secondary"
            disabled={!voiceDebugInfo.logExists}
            style={styles.debugRefreshButton}
          />
          <Button
            label={isExportingVoiceDebugAudio ? 'Sharing...' : 'Share latest voice sample'}
            onPress={() => {
              void exportVoiceDebugAudio();
            }}
            variant="secondary"
            disabled={!voiceDebugInfo.audioExists}
            style={styles.debugRefreshButton}
          />
          <Button
            label="Clear voice debug"
            onPress={() => {
              void clearVoiceDebug();
            }}
            variant="secondary"
            disabled={!voiceDebugInfo.logExists && !voiceDebugInfo.audioExists}
            style={styles.debugRefreshButton}
          />
        </Card>

        <Card style={[styles.card, styles.versionCard]}>
          <Text style={styles.versionLabel}>Event photo debug</Text>
          <Text style={styles.versionValue}>
            Log file present: {eventPhotoDebugInfo.exists ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Log file size: {eventPhotoDebugInfo.sizeBytes} bytes
          </Text>
          <Button
            label={isExportingEventPhotoDebug ? 'Exporting...' : 'Download event photo debug'}
            onPress={() => {
              void exportEventPhotoDebug();
            }}
            variant="secondary"
            disabled={!eventPhotoDebugInfo.exists}
            style={styles.debugRefreshButton}
          />
        </Card>

        <Card style={[styles.card, styles.versionCard]}>
          <Text style={styles.versionLabel}>Background location sync</Text>
          <Text style={styles.versionValue}>
            Location mode: {backgroundStatus?.locationMode ?? 'unknown'}
          </Text>
          <Text style={styles.versionValue}>
            Android capture mode: {backgroundStatus?.androidCaptureMode ?? 'unknown'}
          </Text>
          <Text style={styles.versionValue}>
            Capture distance interval:{' '}
            {backgroundStatus?.configuredDistanceIntervalMeters ?? 'unknown'}m
          </Text>
          <Text style={styles.versionValue}>
            Capture time interval: {backgroundStatus?.configuredTimeIntervalMs ?? 'unknown'}ms
          </Text>
          <Text style={styles.versionValue}>
            Foreground permission: {backgroundStatus?.foregroundPermission ?? 'unknown'}
          </Text>
          <Text style={styles.versionValue}>
            Background permission: {backgroundStatus?.backgroundPermission ?? 'unknown'}
          </Text>
          <Text style={styles.versionValue}>
            Location services enabled:{' '}
            {backgroundStatus?.locationServicesEnabled == null
              ? 'unknown'
              : backgroundStatus.locationServicesEnabled
                ? 'yes'
                : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Background location available:{' '}
            {backgroundStatus?.backgroundLocationAvailable == null
              ? 'unknown'
              : backgroundStatus.backgroundLocationAvailable
                ? 'yes'
                : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            TaskManager available:{' '}
            {backgroundStatus?.taskManagerAvailable == null
              ? 'unknown'
              : backgroundStatus.taskManagerAvailable
                ? 'yes'
                : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Background task defined: {backgroundStatus?.taskDefined ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Continuous location task started: {backgroundStatus?.taskStarted ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Scheduled drain task defined: {backgroundStatus?.drainTaskDefined ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Scheduled drain task registered: {backgroundStatus?.drainTaskRegistered ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Movement geofence defined: {backgroundStatus?.geofenceTaskDefined ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Movement geofence registered: {backgroundStatus?.geofenceTaskRegistered ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Background worker status: {backgroundStatus?.backgroundTaskStatus ?? 'unknown'}
          </Text>
          <Text style={styles.versionValue}>
            Provider status: {formatDebugPayload(backgroundStatus?.providerStatus ?? undefined)}
          </Text>
          <Text style={styles.versionValue}>
            Registered tasks: {formatDebugPayload(backgroundStatus?.registeredTasks ?? undefined)}
          </Text>
          <Text style={styles.versionValue}>
            Location task options:{' '}
            {formatDebugPayload(backgroundStatus?.locationTaskOptions ?? undefined)}
          </Text>
          <Text style={styles.versionValue}>
            Queued background locations: {backgroundStatus?.queuedLocationCount ?? 0}
          </Text>
          <Text style={styles.versionValue}>
            Oldest queued capture: {formatBuildTimestamp(backgroundStatus?.oldestQueuedCapturedAt)}
          </Text>
          <Text style={styles.versionValue}>
            Newest queued capture: {formatBuildTimestamp(backgroundStatus?.newestQueuedCapturedAt)}
          </Text>
          <Text style={styles.versionValue}>
            Background log file: {locationDebugLogInfo.exists ? 'yes' : 'no'}
          </Text>
          <Text style={styles.versionValue}>
            Background log size: {locationDebugLogInfo.sizeBytes} bytes
          </Text>
          <Text style={styles.versionValue}>Background event count: {backgroundEvents.length}</Text>
          <Text style={styles.versionValue}>
            Last background event: {lastBackgroundEvent?.eventName ?? 'none'}
          </Text>
          <Text style={styles.versionValue}>
            Last background event at: {formatBuildTimestamp(lastBackgroundEvent?.at)}
          </Text>
          <Text style={styles.versionValue}>
            Last background message: {lastBackgroundEvent?.message ?? 'none'}
          </Text>
          <Text style={styles.versionValue}>
            Last background error: {lastBackgroundEvent?.error ?? 'none'}
          </Text>
          <Text style={styles.versionValue}>
            Last captured at:{' '}
            {formatBuildTimestamp(getPayloadString(lastBackgroundEvent?.payload, 'captured_at'))}
          </Text>
          <Text style={styles.versionValue}>
            Last batch window:{' '}
            {formatBuildTimestamp(
              getPayloadString(lastBackgroundEvent?.payload, 'batch_first_captured_at'),
            )}{' '}
            -{' '}
            {formatBuildTimestamp(
              getPayloadString(lastBackgroundEvent?.payload, 'batch_last_captured_at'),
            )}
          </Text>
          <Text style={styles.versionValue}>
            Last request URL: {getPayloadString(lastBackgroundEvent?.payload, 'request_url')}
          </Text>
          <Text style={styles.versionValue}>
            Last status: {getPayloadNumber(lastBackgroundEvent?.payload, 'status')}
          </Text>
          <Text style={styles.versionValue}>
            Last content type: {getPayloadString(lastBackgroundEvent?.payload, 'content_type')}
          </Text>
          <Text style={styles.versionValue}>
            Last app state: {getPayloadString(lastBackgroundEvent?.payload, 'app_state')}
          </Text>
          <Text style={styles.versionValue}>
            Last duration: {getPayloadNumber(lastBackgroundEvent?.payload, 'request_duration_ms')}{' '}
            ms
          </Text>
          <Text style={styles.versionValue}>
            Last token present: {getPayloadBoolean(lastBackgroundEvent?.payload, 'token_present')}
          </Text>
          <Text style={styles.versionValue}>
            Last token fingerprint:{' '}
            {getPayloadString(lastBackgroundEvent?.payload, 'token_fingerprint')}
          </Text>
          <Text style={styles.versionValue}>
            Last token expires at:{' '}
            {formatBuildTimestamp(
              getPayloadString(lastBackgroundEvent?.payload, 'token_expires_at'),
            )}
          </Text>
          <Text style={styles.versionValue}>
            Last token expires in:{' '}
            {getPayloadNumber(lastBackgroundEvent?.payload, 'token_expires_in_seconds')}
          </Text>
          <Text style={styles.versionValue}>
            Last token expired:{' '}
            {getPayloadBoolean(lastBackgroundEvent?.payload, 'token_is_expired')}
          </Text>
          <Text style={styles.versionValue}>
            Last sample count: {getPayloadNumber(lastBackgroundEvent?.payload, 'sample_count')}
          </Text>
          <Text style={styles.versionValue}>
            Last payload: {formatDebugPayload(lastBackgroundEvent?.payload)}
          </Text>
          <Text style={styles.versionValue}>
            Last success at: {formatBuildTimestamp(locationDebug.lastSuccessAt)}
          </Text>
          <Text style={styles.versionValue}>
            Total successes: {locationDebug.totalSuccessCount ?? 0}
          </Text>
          <Text style={styles.versionValue}>
            Successes since last failure: {locationDebug.successCountSinceLastFailure ?? 0}
          </Text>
          <Button
            label={isRefreshingLocationDebug ? 'Refreshing...' : 'Refresh location debug'}
            onPress={() => {
              void refreshLocationDebug();
            }}
            variant="secondary"
            style={styles.debugRefreshButton}
          />
          <Button
            label={isExportingLocationDebug ? 'Exporting...' : 'Download background location log'}
            onPress={() => {
              void exportLocationDebug();
            }}
            variant="secondary"
            style={styles.debugRefreshButton}
          />
          <Text style={styles.versionLabel}>Background failures</Text>
          {backgroundFailures.length ? (
            backgroundFailures.map((event) => (
              <LocationDebugEventRow key={`${event.at}-${event.eventName}`} event={event} />
            ))
          ) : (
            <Text style={styles.versionValue}>No stored background failures yet.</Text>
          )}
          <Text style={styles.versionLabel}>Recent background activity</Text>
          {backgroundEvents.length ? (
            backgroundEvents
              .slice(0, 20)
              .map((event) => (
                <LocationDebugEventRow key={`${event.at}-${event.eventName}`} event={event} />
              ))
          ) : (
            <Text style={styles.versionValue}>No stored background activity yet.</Text>
          )}
        </Card>

        <Button label="Sign out" onPress={signOut} variant="primary" style={styles.signOutButton} />
      </Animated.ScrollView>

      <CollapsingTopBar
        title="Settings"
        secondaryTitle="Control your Brain"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 24,
  },
  card: {
    borderRadius: theme.radius.xl,
    padding: 20,
  },
  textBlock: {
    flex: 1,
  },
  rowTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  rowSubtitle: {
    marginTop: 6,
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  navCard: {
    marginTop: 16,
    padding: 0,
    overflow: 'hidden',
  },
  navRow: {
    borderRadius: theme.radius.xl,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 20,
    gap: 12,
  },
  signOutButton: {
    marginTop: 20,
    alignSelf: 'stretch',
    borderRadius: theme.radius.md,
    paddingHorizontal: 18,
    backgroundColor: theme.colors.ink,
  },
  versionCard: {
    marginTop: 16,
    gap: 4,
  },
  versionLabel: {
    marginTop: 4,
    fontSize: 12,
    color: theme.colors.mutedInk,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  versionValue: {
    fontSize: 14,
    color: theme.colors.ink,
    fontWeight: '600',
  },
  debugRefreshButton: {
    marginTop: 8,
    alignSelf: 'stretch',
  },
  debugEventRow: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
    gap: 2,
  },
  debugEventTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  debugEventMeta: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  debugEventPayload: {
    fontSize: 12,
    color: theme.colors.ink,
  },
});
