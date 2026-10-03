import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Alert,
  Animated,
  AppState,
  KeyboardAvoidingView,
  PermissionsAndroid,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { glassesNative } from '@/glasses/runtime';
import { useAppNotice } from '@/hooks/useAppNotice';
import type { GlassesRecordingStatus } from '@/modules/digital-brain-runtime/src';
import Storage from '@/modules/digital-brain-storage/src';
import {
  chooseDigitalBrainStorageBaseUri,
  getDigitalBrainStorageBaseUri,
} from '@/storage/digitalBrainStorage';
import { theme } from '@/theme';

type AudioFile = {
  uri: string;
  name: string;
  mimeType: string;
  bytes: number;
  modifiedAtMs?: number;
};
function recorder() {
  const native = glassesNative();
  if (!native.startGlassesRecording)
    throw new Error('Install a new Android build to record from glasses.');
  return native;
}
function stopPlaybackQuietly() {
  try {
    void recorder()
      .stopGlassesRecordingPlayback()
      .catch(() => undefined);
  } catch {
    // Cleanup also runs in builds without the native recording module.
  }
}
function clock(ms: number) {
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  return `${hours ? `${hours}:` : ''}${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
function size(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function GlassesRecordingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { showError, showSuccess } = useAppNotice();
  const [status, setStatus] = React.useState<GlassesRecordingStatus | null>(null);
  const [files, setFiles] = React.useState<AudioFile[]>([]);
  const [folder, setFolder] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const busyRef = React.useRef(false);
  const [error, setError] = React.useState<string | null>(null);
  const [renaming, setRenaming] = React.useState<AudioFile | null>(null);
  const [name, setName] = React.useState('');
  const [listRevision, setListRevision] = React.useState(0);
  const active = status?.state === 'RECORDING';
  const saving = status?.state === 'SAVING';
  const failedSave = status?.state === 'SAVE_FAILED';
  const playback = status?.playback;

  const refreshStatus = React.useCallback(async () => {
    try {
      setStatus(await recorder().getGlassesRecordingStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Recording status is unavailable.');
    }
  }, []);
  const refreshFiles = React.useCallback(async () => {
    const base = await getDigitalBrainStorageBaseUri();
    setFolder(base);
    if (!base || !Storage) {
      setFiles([]);
      return;
    }
    const entries = await Storage.listSubdirectory(base, 'Recordings');
    setFiles(
      entries
        .filter(
          (f) => f.mimeType.startsWith('audio/') || /\.(m4a|aac|wav|mp3|ogg|flac)$/i.test(f.name),
        )
        .sort(
          (a, b) => (b.modifiedAtMs ?? 0) - (a.modifiedAtMs ?? 0) || b.name.localeCompare(a.name),
        ),
    );
  }, []);
  React.useEffect(() => {
    void refreshFiles().catch((e) =>
      showError(e instanceof Error ? e.message : 'Could not read Recordings.'),
    );
  }, [listRevision, refreshFiles, showError]);
  const lastState = React.useRef<string | undefined>(undefined);
  React.useEffect(() => {
    if (lastState.current && lastState.current !== 'IDLE' && status?.state === 'IDLE')
      setListRevision((n) => n + 1);
    lastState.current = status?.state;
  }, [status?.state]);
  useFocusEffect(
    React.useCallback(() => {
      let live = true;
      const update = () => {
        if (live && AppState.currentState === 'active') void refreshStatus();
      };
      update();
      setListRevision((n) => n + 1);
      const interval = setInterval(update, 1000);
      const listener = AppState.addEventListener('change', (state) => {
        if (state === 'active') {
          update();
          setListRevision((n) => n + 1);
        } else stopPlaybackQuietly();
      });
      return () => {
        live = false;
        clearInterval(interval);
        listener.remove();
        stopPlaybackQuietly();
      };
    }, [refreshStatus]),
  );
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await action();
      await refreshStatus();
      setListRevision((n) => n + 1);
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Recording action failed.');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function baseFolder() {
    const base =
      (await getDigitalBrainStorageBaseUri()) ?? (await chooseDigitalBrainStorageBaseUri());
    if (!base) throw new Error('Choose your Digital Brain folder to save recordings.');
    setFolder(base);
    return base;
  }
  async function start() {
    const base = await baseFolder();
    const required = [
      PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
      PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE,
    ];
    const result = await PermissionsAndroid.requestMultiple(required);
    if (required.some((p) => result[p] !== PermissionsAndroid.RESULTS.GRANTED))
      throw new Error('Allow microphone and Phone access to record and stop on calls.');
    if (Number(Platform.Version) >= 33)
      await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
    await recorder().startGlassesRecording(base);
  }
  function remove(file: AudioFile) {
    Alert.alert(
      'Delete recording?',
      `“${file.name}” will be permanently removed from Recordings.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () =>
            void run(async () => {
              await recorder().stopGlassesRecordingPlayback();
              if (!Storage) throw new Error('Install a new Android build to manage recordings.');
              await Storage.deleteFile(file.uri);
              showSuccess('Recording deleted.');
            }),
        },
      ],
    );
  }
  return (
    <View style={styles.screen}>
      <LinearGradient colors={theme.gradients.sunrise} style={StyleSheet.absoluteFill} />
      <CollapsingTopBar
        title="Recordings"
        secondaryTitle="Glasses"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <Animated.ScrollView
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
            useNativeDriver: false,
          })}
          scrollEventThrottle={16}
          automaticallyAdjustKeyboardInsets
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          contentContainerStyle={[
            styles.content,
            {
              paddingTop:
                insets.top +
                COLLAPSING_TOP_BAR_HEIGHT +
                COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT +
                COLLAPSING_CONTENT_TOP_PADDING,
              paddingBottom: insets.bottom + 40,
            },
          ]}
        >
          {error && (
            <Card style={styles.card}>
              <Text style={styles.error}>{error}</Text>
            </Card>
          )}
          <Card variant="elevated" style={styles.hero}>
            <View style={styles.heroTop}>
              <View style={[styles.badge, active && styles.liveBadge]}>
                <View style={[styles.dot, active && styles.liveDot]} />
                <Text style={[styles.badgeText, active && styles.liveText]}>
                  {active
                    ? 'RECORDING'
                    : saving
                      ? 'SAVING AUDIO'
                      : failedSave
                        ? 'SAVE PENDING'
                        : status?.ready
                          ? 'GLASSES READY'
                          : 'GLASSES OFFLINE'}
                </Text>
              </View>
              <Text style={styles.format}>M4A · AAC</Text>
            </View>
            <View style={[styles.micHalo, active && styles.activeHalo]}>
              <Ionicons
                name={active ? 'mic' : 'mic-outline'}
                size={36}
                color={active ? theme.colors.accentDeep : theme.colors.teal}
              />
            </View>
            <Text style={styles.timer}>{clock(status?.durationMs ?? 0)}</Text>
            <Text style={styles.heroTitle}>
              {active
                ? 'Capture the moment'
                : saving
                  ? 'Saving your recording'
                  : 'Audio from your glasses'}
            </Text>
            <Text style={styles.heroBody}>
              {active
                ? 'Keep your phone locked or leave this screen. Recording continues until you stop or an interruption occurs.'
                : 'Start here. Stop here. Your audio stays in your Recordings folder.'}
            </Text>
            <Button
              label={
                active
                  ? 'Stop & save recording'
                  : saving
                    ? 'Saving…'
                    : failedSave
                      ? 'Retry saving recording'
                      : 'Start recording'
              }
              variant={active ? 'danger' : 'primary'}
              disabled={busy || saving || (!active && !failedSave && !status?.ready)}
              loading={busy}
              onPress={() =>
                void run(async () => {
                  if (active) await recorder().stopGlassesRecording();
                  else if (failedSave)
                    await recorder().retryGlassesRecordingSave(await baseFolder());
                  else await start();
                })
              }
            />
            {status?.message && (
              <Text accessibilityLiveRegion="polite" style={styles.note}>
                {status.message}
              </Text>
            )}
          </Card>
          <View style={styles.libraryHeader}>
            <View>
              <Text style={styles.sectionTitle}>Your recordings</Text>
              <Text style={styles.body}>
                {files.length} audio {files.length === 1 ? 'file' : 'files'} · stored locally
              </Text>
            </View>
            <AppPressable
              accessibilityLabel="Refresh recordings"
              onPress={() => setListRevision((n) => n + 1)}
              style={styles.smallButton}
            >
              <Ionicons name="refresh-outline" size={22} color={theme.colors.teal} />
            </AppPressable>
          </View>
          {!folder && (
            <Card style={styles.card}>
              <Text style={styles.heroTitle}>Choose where your audio lives</Text>
              <Text style={styles.body}>
                Select your main Digital Brain folder. We’ll use its Recordings subfolder.
              </Text>
              <Button
                label="Choose Digital Brain folder"
                variant="secondary"
                onPress={() =>
                  void run(async () => {
                    await baseFolder();
                  })
                }
              />
            </Card>
          )}
          {folder && files.length === 0 && (
            <Card style={styles.empty}>
              <Ionicons name="headset-outline" size={32} color={theme.colors.teal} />
              <Text style={styles.heroTitle}>A place for what you hear</Text>
              <Text style={styles.body}>
                Your first recording will appear here. Audio files already in Recordings appear here
                too.
              </Text>
            </Card>
          )}
          {files.map((file) => {
            const selected = playback?.uri === file.uri;
            return (
              <Card key={file.uri} style={styles.card}>
                <View style={styles.fileTop}>
                  <AppPressable
                    accessibilityLabel={
                      selected && playback?.playing ? `Pause ${file.name}` : `Play ${file.name}`
                    }
                    disabled={busy || active || saving || failedSave}
                    onPress={() =>
                      void run(async () => {
                        await recorder().playGlassesRecording(file.uri);
                      })
                    }
                    style={styles.playButton}
                  >
                    <Ionicons
                      name={selected && playback?.playing ? 'pause' : 'play'}
                      size={22}
                      color={theme.colors.teal}
                    />
                  </AppPressable>
                  <View style={styles.flex}>
                    <Text style={styles.fileName} numberOfLines={2}>
                      {file.name.replace(/\.[^.]+$/, '')}
                    </Text>
                    <Text style={styles.fileMeta}>
                      {file.name.split('.').pop()?.toUpperCase()} · {size(file.bytes)}
                      {file.modifiedAtMs
                        ? ` · ${new Date(file.modifiedAtMs).toLocaleDateString()}`
                        : ''}
                    </Text>
                  </View>
                </View>
                {selected && (
                  <View style={styles.player}>
                    <View style={styles.track}>
                      <View
                        style={[
                          styles.progress,
                          {
                            width: `${Math.min(100, (playback.positionMs / Math.max(1, playback.durationMs)) * 100)}%`,
                          },
                        ]}
                      />
                    </View>
                    <View style={styles.playerRow}>
                      <AppPressable
                        accessibilityLabel="Back 15 seconds"
                        onPress={() =>
                          void run(async () => {
                            await recorder().seekGlassesRecording(playback.positionMs - 15000);
                          })
                        }
                      >
                        <Text style={styles.seek}>−15s</Text>
                      </AppPressable>
                      <Text style={styles.fileMeta}>
                        {clock(playback.positionMs)} / {clock(playback.durationMs)}
                      </Text>
                      <AppPressable
                        accessibilityLabel="Forward 15 seconds"
                        onPress={() =>
                          void run(async () => {
                            await recorder().seekGlassesRecording(playback.positionMs + 15000);
                          })
                        }
                      >
                        <Text style={styles.seek}>+15s</Text>
                      </AppPressable>
                    </View>
                  </View>
                )}
                {renaming?.uri === file.uri ? (
                  <View style={styles.rename}>
                    <Text style={styles.label}>Recording name</Text>
                    <TextInput
                      value={name}
                      onChangeText={setName}
                      style={styles.input}
                      autoFocus
                      maxLength={110}
                      accessibilityLabel="Recording name"
                      returnKeyType="done"
                    />
                    <View style={styles.actions}>
                      <Button
                        label="Cancel"
                        variant="clear"
                        style={styles.flex}
                        onPress={() => setRenaming(null)}
                      />
                      <Button
                        label="Save name"
                        variant="secondary"
                        disabled={busy || !name.trim()}
                        style={styles.flex}
                        onPress={() =>
                          void run(async () => {
                            if (!Storage) throw new Error('Storage is unavailable.');
                            const dot = file.name.lastIndexOf('.');
                            const extension = dot > 0 ? file.name.slice(dot) : '';
                            if (!/^[A-Za-z0-9 _().-]+$/.test(name.trim()))
                              throw new Error(
                                'Use letters, numbers, spaces, periods, parentheses, dashes or underscores.',
                              );
                            const next = name.trim() + extension;
                            if (
                              files.some(
                                (f) =>
                                  f.uri !== file.uri && f.name.toLowerCase() === next.toLowerCase(),
                              )
                            )
                              throw new Error('Another recording already has that name.');
                            await recorder().stopGlassesRecordingPlayback();
                            await Storage.renameDocument(file.uri, next);
                            setRenaming(null);
                          })
                        }
                      />
                    </View>
                  </View>
                ) : (
                  <View style={styles.actions}>
                    <Button
                      label="Rename"
                      variant="clear"
                      disabled={busy || active || saving}
                      style={styles.flex}
                      onPress={() => {
                        setRenaming(file);
                        setName(file.name.replace(/\.[^.]+$/, ''));
                      }}
                    />
                    <Button
                      label="Delete"
                      variant="clear"
                      disabled={busy || active || saving}
                      style={styles.flex}
                      onPress={() => remove(file)}
                    />
                  </View>
                )}
              </Card>
            );
          })}
          {playback?.error && <Text style={styles.error}>{playback.error}</Text>}
          <Text style={styles.footer}>
            Digital Brain / Recordings{'\n'}No upload. No transcription.
          </Text>
        </Animated.ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1 },
  flex: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 16 },
  card: { padding: 18, gap: 12 },
  hero: { padding: 24, gap: 16 },
  heroTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingVertical: 7,
    paddingHorizontal: 10,
    backgroundColor: theme.colors.paleTeal,
    borderRadius: 20,
  },
  liveBadge: { backgroundColor: '#fce6e2' },
  badgeText: { color: theme.colors.teal, fontSize: 10, fontWeight: '700', letterSpacing: 0.8 },
  liveText: { color: theme.colors.accentDeep },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: theme.colors.teal },
  liveDot: { backgroundColor: theme.colors.accent },
  format: { color: theme.colors.mutedInk, fontSize: 11, fontWeight: '600' },
  micHalo: {
    alignSelf: 'center',
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: '#e8f3f2',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 6,
  },
  activeHalo: { backgroundColor: '#fce6e2' },
  timer: {
    fontSize: 48,
    fontWeight: '600',
    fontVariant: ['tabular-nums'],
    letterSpacing: -2,
    color: theme.colors.ink,
    textAlign: 'center',
  },
  heroTitle: { fontSize: 20, fontWeight: '600', color: theme.colors.ink },
  heroBody: { fontSize: 14, lineHeight: 21, color: theme.colors.mutedInk },
  body: { fontSize: 14, lineHeight: 21, color: theme.colors.mutedInk },
  note: { color: theme.colors.teal, fontSize: 13, lineHeight: 19 },
  libraryHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: 8,
  },
  sectionTitle: { fontSize: 22, fontWeight: '700', color: theme.colors.ink, marginBottom: 4 },
  smallButton: { padding: 12, borderRadius: 20 },
  empty: { padding: 26, gap: 14 },
  fileTop: { flexDirection: 'row', gap: 14, alignItems: 'center' },
  playButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: theme.colors.paleTeal,
    alignItems: 'center',
    justifyContent: 'center',
  },
  fileName: { fontSize: 16, fontWeight: '600', color: theme.colors.ink },
  fileMeta: { fontSize: 12, color: theme.colors.mutedInk, marginTop: 5 },
  actions: {
    flexDirection: 'row',
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
    paddingTop: 4,
  },
  player: { gap: 10 },
  track: { height: 4, borderRadius: 2, backgroundColor: theme.colors.line, overflow: 'hidden' },
  progress: { height: 4, backgroundColor: theme.colors.teal },
  playerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  seek: { color: theme.colors.teal, fontWeight: '600', padding: 8 },
  rename: { gap: 10 },
  label: { fontSize: 13, fontWeight: '600', color: theme.colors.mutedInk },
  input: {
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: theme.colors.background,
    color: theme.colors.ink,
    fontSize: 16,
  },
  error: { color: theme.colors.accentDeep, lineHeight: 21 },
  footer: {
    textAlign: 'center',
    fontSize: 12,
    lineHeight: 20,
    color: theme.colors.mutedInk,
    paddingVertical: 8,
  },
});
