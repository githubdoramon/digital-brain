import { useFocusEffect } from '@react-navigation/native';
import * as FileSystem from 'expo-file-system/legacy';
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
import type { SpeechTestModel, SpeechTestStatus } from '@/modules/digital-brain-runtime/src';
import {
  chooseDigitalBrainStorageBaseUri,
  getDigitalBrainStorageBaseUri,
} from '@/storage/digitalBrainStorage';
import { theme } from '@/theme';

function seconds(ms: number | null) {
  return ms == null ? '—' : `${(ms / 1000).toFixed(2)} s`;
}
function percent(value: number | null) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function failureMessage(reason: string) {
  switch (reason) {
    case 'server_update_required':
      return 'Update your server, then start a new comparison.';
    case 'cpu_configuration_mismatch':
      return 'The server could not use the selected settings. Check your server and try again.';
    case 'auth_rejected':
      return 'Sign in again, then retry the comparison.';
    case 'evaluation_disabled':
      return 'Enable speech comparisons on your server, then try again.';
    case 'network_unavailable':
    case 'network_timeout':
    case 'server_timeout':
    case 'tls_failed':
    case 'endpoint_unavailable':
    case 'upstream_unavailable':
      return 'Could not reach your server. Check the connection and try again.';
    case 'server_busy':
      return 'Your server is busy. Try again when it is available.';
    case 'insufficient_memory':
      return 'There was not enough memory. Try a smaller model.';
    case 'cache_storage_full':
      return 'There was not enough storage. Free some space and try again.';
    default:
      return 'Some samples could not be processed. Export the results for details.';
  }
}

export default function SpeechComparisonScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { showError, showSuccess } = useAppNotice();
  const [models, setModels] = React.useState<SpeechTestModel[]>([]);
  const [selected, setSelected] = React.useState([
    'local:base.en',
    'local:small.en',
    'local:small.en-q5_1',
  ]);
  const [folder, setFolder] = React.useState<string | null>(null);
  const [repeats, setRepeats] = React.useState(2);
  const [serverThreads, setServerThreads] = React.useState<number[]>([2]);
  const [status, setStatus] = React.useState<SpeechTestStatus | null>(null);
  const [busy, setBusy] = React.useState(false);
  const busyRef = React.useRef(false);
  const active = status?.state === 'running' || status?.state === 'ready';
  const refreshing = React.useRef(false);
  const refresh = React.useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      setStatus(await glassesNative().getGlassesSpeechTestStatus());
    } catch {
      /* Interactive actions report unavailable builds. */
    } finally {
      refreshing.current = false;
    }
  }, []);
  useFocusEffect(
    React.useCallback(() => {
      let mounted = true;
      void Promise.resolve()
        .then(() => glassesNative().getGlassesSpeechTestModels())
        .then((value) => {
          if (mounted) setModels(value);
        })
        .catch(() => undefined);
      const update = () => {
        if (AppState.currentState === 'active') void refresh();
      };
      update();
      const timer = setInterval(update, 1500);
      const listener = AppState.addEventListener('change', update);
      return () => {
        mounted = false;
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
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not complete this action.');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  return (
    <View style={styles.screen}>
      <LinearGradient colors={theme.gradients.sunrise} style={StyleSheet.absoluteFill} />
      <CollapsingTopBar
        title="Speech comparisons"
        secondaryTitle="Listening"
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
            paddingBottom: insets.bottom + 32,
          },
        ]}
      >
        <Card style={styles.card}>
          <Text style={styles.title}>Recordings</Text>
          <Text style={styles.body}>
            Choose the folder containing your dataset.json and its recordings.
          </Text>
          <Button
            label={folder ? 'Change dataset folder' : 'Choose dataset folder'}
            variant="secondary"
            disabled={busy || active}
            onPress={() =>
              void run(async () => {
                const choice =
                  await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync();
                if (choice.granted) setFolder(choice.directoryUri);
              })
            }
          />
          {folder && <Text style={styles.body}>Dataset folder selected</Text>}
        </Card>
        {(['local', 'server'] as const).map((location) => (
          <Card key={location} style={styles.card}>
            <Text style={styles.title}>
              {location === 'local' ? 'On this phone' : 'On your server'}
            </Text>
            {location === 'server' && (
              <Text style={styles.body}>
                Selected recordings are sent to your Digital Brain server for comparison.
              </Text>
            )}
            {models
              .filter((model) => model.location === location)
              .map((model) => (
                <View key={model.id} style={styles.row}>
                  <View style={styles.flex}>
                    <Text style={styles.label}>{model.label}</Text>
                    {model.bytes > 0 && (
                      <Text style={styles.body}>{Math.round(model.bytes / 1024 / 1024)} MB</Text>
                    )}
                  </View>
                  <Switch
                    accessibilityLabel={`Include ${model.label} ${location === 'local' ? 'on phone' : 'on server'}`}
                    disabled={busy || active}
                    value={selected.includes(model.id)}
                    onValueChange={(on) =>
                      setSelected((current) =>
                        on ? [...current, model.id] : current.filter((id) => id !== model.id),
                      )
                    }
                  />
                </View>
              ))}
          </Card>
        ))}
        {selected.some((id) => id.startsWith('server:')) && (
          <Card style={styles.card}>
            <Text style={styles.title}>Server CPU threads</Text>
            <Text style={styles.body}>
              Each selected server model runs separately at every selected thread count.
            </Text>
            {[2, 4, 8].map((count) => (
              <View key={count} style={styles.row}>
                <Text style={styles.label}>{count} threads</Text>
                <Switch
                  accessibilityLabel={`Compare ${count} server CPU threads`}
                  disabled={busy || active}
                  value={serverThreads.includes(count)}
                  onValueChange={(enabled) =>
                    setServerThreads((current) =>
                      enabled
                        ? [...current, count].sort((a, b) => a - b)
                        : current.filter((value) => value !== count),
                    )
                  }
                />
              </View>
            ))}
          </Card>
        )}
        <Card style={styles.card}>
          <Text style={styles.title}>Repetitions</Text>
          <Text style={styles.body}>
            Each recording runs once from a fresh model, followed by repeated runs with the model
            ready.
          </Text>
          <View style={styles.row}>
            <Button
              label="Fewer"
              variant="secondary"
              disabled={busy || active || repeats <= 1}
              onPress={() => setRepeats((n) => n - 1)}
            />
            <Text style={styles.label}>{repeats} warm runs</Text>
            <Button
              label="More"
              variant="secondary"
              disabled={busy || active || repeats >= 5}
              onPress={() => setRepeats((n) => n + 1)}
            />
          </View>
          <Button
            label="Start new comparison"
            disabled={
              busy ||
              active ||
              !folder ||
              selected.length === 0 ||
              (selected.some((id) => id.startsWith('server:')) && serverThreads.length === 0)
            }
            loading={busy}
            onPress={() =>
              void run(async () => {
                if (Platform.OS === 'android' && Number(Platform.Version) >= 33)
                  await PermissionsAndroid.request(
                    PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
                  );
                const candidates = selected.flatMap((id) =>
                  id.startsWith('server:')
                    ? serverThreads.map((threads) => `${id}@threads=${threads}`)
                    : [id],
                );
                await glassesNative().startGlassesSpeechTest(folder!, candidates, repeats);
              })
            }
          />
        </Card>
        {status && status.state !== 'empty' && (
          <Card style={styles.card}>
            <Text style={styles.title}>{status.state === 'complete' ? 'Results' : 'Progress'}</Text>
            <Text style={styles.label}>
              {status.completed} / {status.total}
            </Text>
            {!!status.message && <Text style={styles.body}>{status.message}</Text>}
            {active && (
              <Text style={styles.body}>
                Listening pauses while comparisons run. You can lock your phone.
              </Text>
            )}
            {active && (
              <Button
                label="Pause"
                variant="secondary"
                disabled={busy}
                onPress={() => void run(() => glassesNative().pauseGlassesSpeechTest())}
              />
            )}
            {status.state === 'paused' && (
              <Button
                label="Resume"
                disabled={busy}
                onPress={() => void run(() => glassesNative().resumeGlassesSpeechTest())}
              />
            )}
            <Button
              label="Export results"
              variant="secondary"
              disabled={busy || status.completed === 0}
              onPress={() =>
                void run(async () => {
                  const base =
                    (await getDigitalBrainStorageBaseUri()) ??
                    (await chooseDigitalBrainStorageBaseUri());
                  if (!base) return;
                  await glassesNative().exportGlassesSpeechTest(base);
                  showSuccess('Results saved in Exports → Speech tests.');
                })
              }
            />
          </Card>
        )}
        {status?.summary
          .filter((row) => row.count > 0)
          .map((row) => (
            <Card key={`${row.candidate}:${row.mode}`} style={styles.card}>
              <Text style={styles.title}>
                {row.candidate
                  .replace('local:', 'Phone · ')
                  .replace('server:', 'Server · ')
                  .replace(/@threads=(\d+)/, ' · $1 threads')}
              </Text>
              <Text style={styles.label}>
                {row.mode === 'cold' ? 'Fresh model' : 'Model ready'}
              </Text>
              <Text style={styles.body}>
                Correct: {row.correct}/{row.completed} ({percent(row.accuracy)}) · Failed:{' '}
                {row.failures}
              </Text>
              {Array.from(new Set(Object.keys(row.failureReasons ?? {}).map(failureMessage))).map(
                (message) => (
                  <Text key={message} style={styles.body}>
                    {message}
                  </Text>
                ),
              )}
              <Text style={styles.body}>
                Missed: {row.missed}/{row.positives} · Wrong command: {row.wrong}/{row.positives}
              </Text>
              <Text style={styles.body}>
                False acceptance: {row.falseAccepts}/{row.negatives}
              </Text>
              <Text style={styles.body}>Word error rate: {percent(row.wer)}</Text>
              <Text style={styles.body}>
                Median: {seconds(row.medianMs)} · p95: {seconds(row.p95Ms)}
              </Text>
            </Card>
          ))}
      </Animated.ScrollView>
    </View>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 16 },
  card: { padding: 20, gap: 14 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  flex: { flex: 1 },
  title: { color: theme.colors.ink, fontSize: 20, fontWeight: '600' },
  label: { color: theme.colors.ink, fontSize: 15, fontWeight: '600' },
  body: { color: theme.colors.mutedInk, fontSize: 14, lineHeight: 21 },
});
