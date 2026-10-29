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
import type { SpeechTestStatus } from '@/modules/digital-brain-runtime/src';
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
    case 'remote_not_configured':
      return 'Configure the GPU server, then try again.';
    case 'remote_http_error':
    case 'remote_invalid_response':
      return 'The GPU server could not process the recording. Export the results for details.';
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
      return 'There was not enough memory. Close other apps and try again.';
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
  const [folder, setFolder] = React.useState<string | null>(null);
  const [repeats, setRepeats] = React.useState(2);
  const [models, setModels] = React.useState([
    'remote:large-v3-turbo',
    'remote:large-v3-turbo@commands-v1',
    'remote:parakeet-tdt-0.6b-v3',
  ]);
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
      const update = () => {
        if (AppState.currentState === 'active') void refresh();
      };
      update();
      const timer = setInterval(update, 1500);
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
        <Card style={styles.card}>
          <Text style={styles.title}>Models</Text>
          {[
            ['remote:large-v3-turbo', 'Whisper Turbo'],
            ['remote:parakeet-tdt-0.6b-v3', 'Parakeet v3'],
            ['remote:large-v3-turbo@commands-v1', 'Whisper Turbo · Command vocabulary'],
            ['remote:parakeet-tdt-0.6b-v3@commands-v1', 'Parakeet v3 · Command vocabulary'],
          ].map(([id, label]) => (
            <View key={id} style={styles.row}>
              <Text style={styles.label}>{label}</Text>
              <Switch
                accessibilityLabel={`Include ${label}`}
                disabled={busy || active}
                value={models.includes(id)}
                onValueChange={(enabled) =>
                  setModels((current) =>
                    enabled ? [...current, id] : current.filter((model) => model !== id),
                  )
                }
              />
            </View>
          ))}
        </Card>
        <Card style={styles.card}>
          <Text style={styles.title}>Repetitions</Text>
          <Text style={styles.body}>
            Each recording is sent to your server for recognition, followed by repeated requests.
          </Text>
          <View style={styles.row}>
            <Button
              label="Fewer"
              variant="secondary"
              disabled={busy || active || repeats <= 1}
              onPress={() => setRepeats((n) => n - 1)}
            />
            <Text style={styles.label}>{repeats} repeat runs</Text>
            <Button
              label="More"
              variant="secondary"
              disabled={busy || active || repeats >= 5}
              onPress={() => setRepeats((n) => n + 1)}
            />
          </View>
          <Button
            label="Start new comparison"
            disabled={busy || active || !folder || models.length === 0}
            loading={busy}
            onPress={() =>
              void run(async () => {
                if (Platform.OS === 'android' && Number(Platform.Version) >= 33)
                  await PermissionsAndroid.request(
                    PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
                  );
                await glassesNative().startGlassesSpeechTest(folder!, models, repeats);
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
                  .replace('remote:', 'GPU server · ')
                  .replace(/@threads=(\d+)/, ' · $1 threads')
                  .replace('@commands-v1', ' · Command vocabulary')}
              </Text>
              <Text style={styles.label}>
                {
                  {
                    cold: 'Fresh model',
                    warm: 'Model ready',
                    first: 'First request',
                    repeat: 'Repeated requests',
                  }[row.mode]
                }
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
