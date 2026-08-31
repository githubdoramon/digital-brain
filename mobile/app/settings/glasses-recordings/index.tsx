import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React from 'react';
import { Alert, Platform, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import {
  deleteGlassesAudioRecording,
  getGlassesAudioRecordingState,
  hydrateGlassesAudioRecording,
  listGlassesAudioRecordings,
  playOrStopGlassesAudioRecording,
  renameGlassesAudioRecording,
  startGlassesAudioRecording,
  stopGlassesAudioRecording,
  subscribeGlassesAudioRecording,
  type GlassesAudioRecording,
} from '@/mentraCapture';
import { getDigitalBrainStorageBaseUri } from '@/storage/digitalBrainStorage';
import { theme } from '@/theme';

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function GlassesRecordingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showError, showSuccess } = useAppNotice();
  const [recordings, setRecordings] = React.useState<GlassesAudioRecording[]>([]);
  const [state, setState] = React.useState(getGlassesAudioRecordingState());
  const [pendingAction, setPendingAction] = React.useState<'start' | 'stop' | null>(null);
  const [renameId, setRenameId] = React.useState<string | null>(null);
  const [renameValue, setRenameValue] = React.useState('');
  const [, setClock] = React.useState(Date.now());
  const [hasStorage, setHasStorage] = React.useState(false);

  const refresh = React.useCallback(async () => {
    const [nextRecordings, baseUri] = await Promise.all([
      listGlassesAudioRecordings(),
      getDigitalBrainStorageBaseUri(),
    ]);
    setRecordings(nextRecordings);
    setHasStorage(Boolean(baseUri));
    await hydrateGlassesAudioRecording();
  }, []);

  useFocusEffect(
    React.useCallback(() => {
      void refresh().catch((error) => {
        showError(error instanceof Error ? error.message : 'Could not load recordings.');
      });
    }, [refresh, showError]),
  );

  React.useEffect(() => subscribeGlassesAudioRecording(setState), []);
  React.useEffect(() => {
    if (!state.recording) return;
    const interval = setInterval(() => setClock(Date.now()), 1_000);
    return () => clearInterval(interval);
  }, [state.recording]);

  const start = async () => {
    setPendingAction('start');
    try {
      await startGlassesAudioRecording();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not start recording.');
    } finally {
      setPendingAction(null);
    }
  };

  const stop = async () => {
    setPendingAction('stop');
    try {
      await stopGlassesAudioRecording();
      await refresh();
      showSuccess('Recording saved to your Digital Brain folder.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not stop recording.');
    } finally {
      setPendingAction(null);
    }
  };

  const remove = (recording: GlassesAudioRecording) => {
    Alert.alert('Delete recording?', `Delete ${recording.name} from the Digital Brain folder?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () => {
          void deleteGlassesAudioRecording(recording)
            .then(refresh)
            .catch((error) =>
              showError(error instanceof Error ? error.message : 'Could not delete recording.'),
            );
        },
      },
    ]);
  };

  const saveRename = async (recording: GlassesAudioRecording) => {
    try {
      const updated = await renameGlassesAudioRecording(recording, renameValue);
      setRecordings((current) =>
        current.map((item) => (item.id === recording.id ? updated : item)),
      );
      setRenameId(null);
      showSuccess('Recording renamed.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not rename recording.');
    }
  };

  const elapsed = state.recording && state.startedAt ? Date.now() - state.startedAt : 0;
  const recordingActionLabel =
    pendingAction === 'start'
      ? 'Starting recording…'
      : pendingAction === 'stop'
        ? 'Saving recording…'
        : state.recording
          ? 'Stop recording'
          : 'Start recording';

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 28 },
        ]}
      >
        <Pressable style={styles.back} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={theme.colors.ink} />
          <Text style={styles.backText}>Smart glasses</Text>
        </Pressable>
        <View style={styles.hero}>
          <View style={styles.heroIcon}>
            <Ionicons name="mic-outline" size={29} color={theme.colors.teal} />
          </View>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>MENTRA LIVE</Text>
            <Text style={styles.title}>Glasses recordings</Text>
            <Text style={styles.subtitle}>
              Record from the glasses microphone with app controls.
            </Text>
          </View>
        </View>
        {Platform.OS !== 'android' ? (
          <Text style={styles.warning}>Recording is currently Android-only.</Text>
        ) : null}
        {!hasStorage ? (
          <Card style={styles.noticeCard}>
            <Text style={styles.noticeTitle}>Choose storage first</Text>
            <Text style={styles.value}>
              Recordings are saved directly to your shared Digital Brain folder.
            </Text>
            <Button
              label="Open storage settings"
              variant="secondary"
              onPress={() => router.push('/settings/storage' as never)}
            />
          </Card>
        ) : null}
        <Card style={styles.recordCard}>
          <View style={styles.recordStatus}>
            <View style={[styles.recordDot, state.recording && styles.recordDotActive]} />
            <Text style={styles.recordStatusText}>
              {state.recording ? `Recording · ${formatDuration(elapsed)}` : 'Ready to record'}
            </Text>
          </View>
          <Text style={styles.value}>
            {state.recording
              ? 'Digital Brain keeps recording while the app is backgrounded or the phone is locked.'
              : 'Only the app controls recording. Your glasses photo and video buttons keep their existing behavior.'}
          </Text>
          <Button
            label={recordingActionLabel}
            variant={state.recording ? 'danger' : 'primary'}
            onPress={() => void (state.recording ? stop() : start())}
            disabled={
              pendingAction !== null ||
              Platform.OS !== 'android' ||
              (!state.recording && !hasStorage)
            }
          />
        </Card>
        <Text style={styles.sectionTitle}>Saved recordings</Text>
        {recordings.length === 0 ? (
          <Card style={styles.card}>
            <Text style={styles.value}>No recordings yet.</Text>
          </Card>
        ) : (
          recordings.map((recording) => (
            <Card key={recording.id} style={styles.card}>
              <View style={styles.recordingHeader}>
                <View style={styles.recordingIcon}>
                  <Ionicons name="musical-notes-outline" size={20} color={theme.colors.teal} />
                </View>
                <View style={styles.recordingCopy}>
                  <Text style={styles.recordingName} numberOfLines={1}>
                    {recording.name}
                  </Text>
                  <Text style={styles.meta}>
                    {new Date(recording.startedAt).toLocaleString()} ·{' '}
                    {formatDuration(recording.durationMs)} · {formatSize(recording.sizeBytes)}
                  </Text>
                </View>
              </View>
              {renameId === recording.id ? (
                <View style={styles.renameRow}>
                  <TextInput
                    value={renameValue}
                    onChangeText={setRenameValue}
                    autoFocus
                    style={styles.renameInput}
                    placeholder="Recording name"
                  />
                  <Button
                    label="Save"
                    variant="secondary"
                    onPress={() => void saveRename(recording)}
                  />
                </View>
              ) : null}
              <View style={styles.actions}>
                <Button
                  label={state.isPlayingUri === recording.uri ? 'Stop' : 'Play'}
                  variant="secondary"
                  onPress={() =>
                    void playOrStopGlassesAudioRecording(recording).catch((error) =>
                      showError(
                        error instanceof Error ? error.message : 'Could not play recording.',
                      ),
                    )
                  }
                />
                <Button
                  label="Rename"
                  variant="secondary"
                  onPress={() => {
                    setRenameId(recording.id);
                    setRenameValue(recording.name.replace(/\.m4a$/i, ''));
                  }}
                />
                <Button label="Delete" variant="danger" onPress={() => remove(recording)} />
              </View>
            </Card>
          ))
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
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
  warning: { color: theme.colors.accentDeep },
  card: { padding: 16, gap: 12 },
  recordCard: { padding: 16, gap: 12, borderColor: theme.colors.teal, borderWidth: 1 },
  noticeCard: { padding: 16, gap: 10, backgroundColor: 'rgba(217,236,235,0.7)' },
  noticeTitle: { color: theme.colors.ink, fontSize: 17, fontWeight: '700' },
  value: { color: theme.colors.mutedInk, lineHeight: 20 },
  recordStatus: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  recordDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: theme.colors.mutedInk },
  recordDotActive: { backgroundColor: theme.colors.accent },
  recordStatusText: { color: theme.colors.ink, fontSize: 17, fontWeight: '700' },
  sectionTitle: { color: theme.colors.ink, fontSize: 19, fontWeight: '700', marginTop: 6 },
  recordingHeader: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  recordingIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  recordingCopy: { flex: 1, gap: 2 },
  recordingName: { color: theme.colors.ink, fontWeight: '600', fontSize: 16 },
  meta: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 18 },
  actions: { flexDirection: 'row', gap: 8 },
  renameRow: { gap: 8 },
  renameInput: {
    borderColor: theme.colors.line,
    borderWidth: 1,
    borderRadius: theme.radius.md,
    color: theme.colors.ink,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
});
