import { useFocusEffect } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
  AppState,
  PermissionsAndroid,
  Platform,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { prepareEnglishWhisperModel } from '@/chat/localTranscription';
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
import type { GlassesListeningStatus } from '@/modules/digital-brain-runtime/src';
import {
  chooseDigitalBrainStorageBaseUri,
  getDigitalBrainStorageBaseUri,
} from '@/storage/digitalBrainStorage';
import { theme } from '@/theme';

export default function GlassesListeningScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { showError, showSuccess } = useAppNotice();
  const [status, setStatus] = React.useState<GlassesListeningStatus | null>(null);
  const [busy, setBusy] = React.useState(false);
  const busyRef = React.useRef(false);
  const [setup, setSetup] = React.useState<string | null>(null);
  const refresh = React.useCallback(async () => {
    try {
      setStatus(await glassesNative().getGlassesListeningStatus());
    } catch {
      /* An unavailable build is reported by an interactive action. */
    }
  }, []);
  useFocusEffect(
    React.useCallback(() => {
      const update = () => {
        if (AppState.currentState === 'active') void refresh();
      };
      update();
      const timer = setInterval(update, 500);
      const listener = AppState.addEventListener('change', update);
      return () => {
        clearInterval(timer);
        listener.remove();
      };
    }, [refresh]),
  );
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not complete this action.');
    } finally {
      busyRef.current = false;
      setBusy(false);
      setSetup(null);
    }
  }
  async function enable(value: boolean) {
    let model: string | null = null;
    if (value) {
      const required = [
        PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
        PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE,
      ];
      const results = await PermissionsAndroid.requestMultiple(required);
      if (required.some((p) => results[p] !== PermissionsAndroid.RESULTS.GRANTED))
        throw new Error('Allow microphone and Phone access to listen and pause for calls.');
      if (Platform.OS === 'android' && Number(Platform.Version) >= 33)
        await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
      setSetup('Preparing speech recognition…');
      model = await prepareEnglishWhisperModel((progress) =>
        setSetup(
          progress.stage === 'downloading_model'
            ? `Preparing speech recognition… ${Math.round(progress.progress ?? 0)}%`
            : 'Preparing speech recognition…',
        ),
      );
    }
    await glassesNative().setGlassesListening(value, model);
  }
  return (
    <View style={styles.screen}>
      <LinearGradient colors={theme.gradients.sunrise} style={StyleSheet.absoluteFill} />
      <CollapsingTopBar
        title="Listening"
        secondaryTitle="Glasses"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
      <Animated.ScrollView
        scrollEventThrottle={16}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
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
        <Card style={styles.card}>
          <View style={styles.row}>
            <View style={styles.flex}>
              <Text style={styles.title}>Listen through glasses</Text>
              <Text style={styles.body}>Save spoken English as timestamped transcripts.</Text>
            </View>
            <Switch
              accessibilityLabel="Listen through glasses"
              value={status?.enabled ?? false}
              disabled={busy || !status}
              onValueChange={(value) => void run(() => enable(value))}
              trackColor={{ true: theme.colors.teal }}
            />
          </View>
          <Text accessibilityLiveRegion="polite" style={styles.status}>
            {setup ?? status?.status ?? 'Loading…'}
          </Text>
          <Text style={styles.body}>
            Listening continues when your phone is locked. Calls and recordings pause it.
          </Text>
        </Card>
        <Card style={styles.card}>
          <Text style={styles.title}>Minimum speech volume</Text>
          <Text style={styles.body}>
            Raise the minimum to ignore quieter sounds. Speak normally and keep it below your voice
            level.
          </Text>
          <View
            accessible
            accessibilityLabel="Current audio level"
            accessibilityRole="progressbar"
            accessibilityValue={{ min: 0, max: 100, now: status?.audioLevel ?? 0 }}
            style={styles.meter}
          >
            <View style={[styles.meterFill, { width: `${status?.audioLevel ?? 0}%` }]} />
            {!!status?.minimumVolume && (
              <View style={[styles.minimumMarker, { left: `${status.minimumVolume}%` }]} />
            )}
          </View>
          <Text style={styles.body}>
            {!status?.enabled
              ? 'Turn listening on to see your voice level.'
              : !status.active
                ? 'The level meter resumes when listening resumes.'
                : 'Speak to see your voice level.'}
          </Text>
          <View style={styles.row}>
            <Button
              label="Lower"
              variant="secondary"
              style={styles.flex}
              disabled={busy || !status || status.minimumVolume === 0}
              onPress={() =>
                void run(async () => {
                  await glassesNative().setGlassesListeningMinimumVolume(
                    Math.max(0, (status?.minimumVolume ?? 0) - 5),
                  );
                })
              }
            />
            <Text style={styles.volumeValue}>
              {status?.minimumVolume ? `${status.minimumVolume}%` : 'Off'}
            </Text>
            <Button
              label="Higher"
              variant="secondary"
              style={styles.flex}
              disabled={busy || !status || status.minimumVolume === 100}
              onPress={() =>
                void run(async () => {
                  await glassesNative().setGlassesListeningMinimumVolume(
                    Math.min(100, (status?.minimumVolume ?? 0) + 5),
                  );
                })
              }
            />
          </View>
          <Text style={styles.body}>A higher minimum may also skip quiet speech.</Text>
        </Card>
        <Card style={styles.card}>
          <Text style={styles.title}>Transcripts</Text>
          <Text style={styles.count}>{status?.count ?? 0}</Text>
          <Text style={styles.body}>
            {status?.pending
              ? `${status.pending} speech ${status.pending === 1 ? 'segment' : 'segments'} being processed`
              : 'Saved transcripts'}
          </Text>
          {!!status?.lastTranscriptAtMs && (
            <Text style={styles.body}>
              Last saved {new Date(status.lastTranscriptAtMs).toLocaleString()}
            </Text>
          )}
          <Button
            label="Export transcripts"
            variant="secondary"
            disabled={busy || !status?.count}
            loading={busy && !setup}
            onPress={() =>
              void run(async () => {
                const base =
                  (await getDigitalBrainStorageBaseUri()) ??
                  (await chooseDigitalBrainStorageBaseUri());
                if (!base) return;
                await glassesNative().exportGlassesTranscripts(base);
                showSuccess('Transcripts saved to your Exports folder.');
              })
            }
          />
        </Card>
      </Animated.ScrollView>
    </View>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 16 },
  card: { padding: 20, gap: 14 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  flex: { flex: 1 },
  title: { color: theme.colors.ink, fontSize: 20, fontWeight: '600', marginBottom: 6 },
  body: { color: theme.colors.mutedInk, fontSize: 14, lineHeight: 21 },
  status: { color: theme.colors.teal, fontSize: 16, fontWeight: '600' },
  meter: { height: 16, borderRadius: 8, backgroundColor: '#e3e5e0', overflow: 'hidden' },
  meterFill: { height: '100%', backgroundColor: theme.colors.teal },
  minimumMarker: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    width: 3,
    backgroundColor: theme.colors.ink,
    transform: [{ translateX: -3 }],
  },
  volumeValue: {
    minWidth: 48,
    textAlign: 'center',
    color: theme.colors.ink,
    fontSize: 16,
    fontWeight: '600',
  },
  count: {
    color: theme.colors.ink,
    fontSize: 44,
    fontWeight: '600',
    fontVariant: ['tabular-nums'],
  },
});
