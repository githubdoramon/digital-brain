import { useFocusEffect } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
  AppState,
  KeyboardAvoidingView,
  PermissionsAndroid,
  Platform,
  StyleSheet,
  Switch,
  Text,
  TextInput,
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
import type { GlassesAlertApp, GlassesAlertsStatus } from '@/modules/digital-brain-runtime/src';
import { theme } from '@/theme';

type Config = Pick<
  GlassesAlertsStatus,
  'notifications' | 'calls' | 'packages' | 'chimeVolume' | 'callVolume'
>;

function nativeAlerts() {
  const native = glassesNative();
  if (!native.getGlassesAlertsStatus)
    throw new Error('Install a new Android native build to enable glasses alerts.');
  return native;
}

function Volume({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <View style={styles.volume}>
      <Text style={styles.body}>
        {label}: {value}%
      </Text>
      <View style={styles.row}>
        <Button
          label={`Lower ${label.toLowerCase()}`}
          variant="secondary"
          disabled={disabled || value === 0}
          onPress={() => onChange(Math.max(0, value - 5))}
          style={styles.flex}
        />
        <Button
          label={`Raise ${label.toLowerCase()}`}
          variant="secondary"
          disabled={disabled || value === 100}
          onPress={() => onChange(Math.min(100, value + 5))}
          style={styles.flex}
        />
      </View>
    </View>
  );
}

export default function GlassesAlertsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { showError } = useAppNotice();
  const [status, setStatus] = React.useState<GlassesAlertsStatus | null>(null);
  const [apps, setApps] = React.useState<GlassesAlertApp[]>([]);
  const [query, setQuery] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const busyRef = React.useRef(false);
  const [error, setError] = React.useState<string | null>(null);
  const refresh = React.useCallback(async () => {
    try {
      setStatus(await nativeAlerts().getGlassesAlertsStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read alert settings.');
    }
  }, []);
  useFocusEffect(
    React.useCallback(() => {
      let active = true;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const poll = async () => {
        if (!active || AppState.currentState !== 'active') return;
        await refresh();
        if (active) timer = setTimeout(() => void poll(), 1500);
      };
      const initialize = async () => {
        try {
          const native = nativeAlerts();
          await native.refreshGlassesAlerts();
          const list = await native.getGlassesAlertApps();
          if (active) setApps(list);
        } catch (e) {
          if (active) setError(e instanceof Error ? e.message : 'Could not load apps.');
        }
      };
      void initialize();
      void poll();
      const subscription = AppState.addEventListener('change', (state) => {
        if (timer) clearTimeout(timer);
        if (state === 'active') {
          void initialize();
          void poll();
        }
      });
      return () => {
        active = false;
        if (timer) clearTimeout(timer);
        subscription.remove();
        // Stop a preview on leaving; never interrupt a real incoming call.
        try {
          void nativeAlerts()
            .stopGlassesAlertTest()
            .catch(() => {});
        } catch {
          /* Older native build. */
        }
      };
    }, [refresh]),
  );

  const run = async (action: () => Promise<unknown>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Glasses alert action failed.');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const save = (change: Partial<Config>) =>
    void run(async () => {
      if (!status) return;
      const next = { ...status, ...change };
      await nativeAlerts().saveGlassesAlerts(
        next.notifications,
        next.calls,
        next.packages,
        next.chimeVolume,
        next.callVolume,
      );
    });
  const allowCalls = () =>
    void run(async () => {
      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE,
      );
      if (granted !== PermissionsAndroid.RESULTS.GRANTED)
        throw new Error('Allow Phone access in Android Settings to detect cellular calls.');
      await nativeAlerts().refreshGlassesAlerts();
    });
  const visibleApps = apps.filter((app) =>
    `${app.label} ${app.packageName}`.toLowerCase().includes(query.toLowerCase()),
  );
  const canTest =
    status?.audioReady === true && status?.mediaMuted === false && !status?.dndBlocksMedia;
  return (
    <View style={styles.screen}>
      <LinearGradient colors={theme.gradients.sunrise} style={StyleSheet.absoluteFill} />
      <CollapsingTopBar
        title="Alerts"
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
              paddingBottom: insets.bottom + 32,
            },
          ]}
        >
          {error && (
            <Card style={styles.card}>
              <Text style={styles.error}>{error}</Text>
              <Button label="Retry" variant="secondary" onPress={() => void refresh()} />
            </Card>
          )}
          {status && (
            <>
              <Card style={styles.card}>
                <View style={styles.row}>
                  <Text style={[styles.title, styles.flex]}>Notification chimes</Text>
                  <Switch
                    accessibilityLabel="Enable notification chimes"
                    value={status.notifications}
                    disabled={busy}
                    onValueChange={(notifications) => save({ notifications })}
                  />
                </View>
                <Text style={styles.body}>
                  Only selected apps can chime. Chimes stay quiet when the screen is on and
                  unlocked, with at most one every five seconds.
                </Text>
                <Volume
                  label="Chime volume"
                  value={status.chimeVolume}
                  disabled={busy}
                  onChange={(chimeVolume) => save({ chimeVolume })}
                />
                <Button
                  label="Test chime"
                  variant="secondary"
                  disabled={busy || !canTest || status.playing === 'call'}
                  onPress={() => void run(() => nativeAlerts().testGlassesAlert(false))}
                />
              </Card>
              <Card style={styles.card}>
                <View style={styles.row}>
                  <Text style={[styles.title, styles.flex]}>Incoming calls</Text>
                  <Switch
                    accessibilityLabel="Enable incoming call alerts"
                    value={status.calls}
                    disabled={busy}
                    onValueChange={(calls) => save({ calls })}
                  />
                </View>
                <Text style={styles.body}>
                  A repeating ring for cellular and app calls, even while you use the phone. It
                  stops when the call is answered, declined, or ends.
                </Text>
                <Text style={styles.body}>
                  App calls must expose an incoming-call notification to Android. Some apps may not
                  provide enough information.
                </Text>
                <Volume
                  label="Call volume"
                  value={status.callVolume}
                  disabled={busy}
                  onChange={(callVolume) => save({ callVolume })}
                />
                <Button
                  label="Test call ring"
                  variant="secondary"
                  disabled={busy || !canTest || status.playing === 'call'}
                  onPress={() => void run(() => nativeAlerts().testGlassesAlert(true))}
                />
                {status.playing === 'preview' && (
                  <Button
                    label="Stop test"
                    variant="clear"
                    disabled={busy}
                    onPress={() => void run(() => nativeAlerts().stopGlassesAlertTest())}
                  />
                )}
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Setup and sound</Text>
                <Text style={styles.body}>
                  {status.notificationAccess
                    ? status.listenerConnected
                      ? 'Notification access is ready.'
                      : 'Notification access is allowed; waiting for Android to connect.'
                    : 'Allow notification access to detect selected apps and incoming app calls.'}
                </Text>
                <Button
                  label="Notification access"
                  variant="secondary"
                  disabled={busy}
                  onPress={() => void run(() => nativeAlerts().openGlassesNotificationAccess())}
                />
                <Text style={styles.body}>
                  {status.phonePermission
                    ? 'Cellular call access is allowed.'
                    : 'Allow Phone access to detect cellular ringing. Caller names and numbers are not used.'}
                </Text>
                {!status.phonePermission && (
                  <Button
                    label="Allow cellular calls"
                    variant="secondary"
                    disabled={busy}
                    onPress={allowCalls}
                  />
                )}
                <Text style={styles.body}>
                  {status.audioReady
                    ? 'Glasses audio is connected.'
                    : 'Enable and connect your glasses, including their Bluetooth media audio output in Android Settings.'}
                </Text>
                <Text style={styles.body}>
                  Music and podcasts briefly lower while alerts play. Android controls ducking; some
                  players may pause instead.
                </Text>
                <Text style={styles.body}>
                  Alerts use Bluetooth media audio, so silent/vibrate mode does not suppress them.
                  Do Not Disturb still applies when it blocks media.
                </Text>
                {status.dndActive && (
                  <Text style={styles.body}>
                    Do Not Disturb is currently active.{' '}
                    {status.dndBlocksMedia
                      ? 'Media alerts are suppressed.'
                      : 'Android sound policy still applies.'}
                  </Text>
                )}
                {status.mediaMuted && (
                  <Text style={styles.error}>
                    Bluetooth media volume is muted. Raise it to hear alerts.
                  </Text>
                )}
                <Text style={styles.body}>
                  These volume controls adjust the alert within your Bluetooth media volume. Alerts
                  never fall back to the phone speaker.
                </Text>
                <Button
                  label="Android sound settings"
                  variant="clear"
                  disabled={busy}
                  onPress={() => void run(() => nativeAlerts().openGlassesSoundSettings())}
                />
                {status.lastPlaybackError && (
                  <Text style={styles.error}>{status.lastPlaybackError}</Text>
                )}
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Apps that can chime · {status.packages.length}</Text>
                <Text style={styles.body}>
                  Start with none selected. Call alerts work independently of this list.
                </Text>
                <TextInput
                  accessibilityLabel="Search notification apps"
                  placeholder="Search apps"
                  value={query}
                  onChangeText={setQuery}
                  autoCorrect={false}
                  style={styles.input}
                />
                {visibleApps.length === 0 && <Text style={styles.body}>No matching apps.</Text>}
                {visibleApps.map((app) => (
                  <View key={app.packageName} style={styles.appRow}>
                    <Text style={[styles.body, styles.flex]}>{app.label}</Text>
                    <Switch
                      accessibilityLabel={`Chimes for ${app.label}`}
                      disabled={busy}
                      value={status.packages.includes(app.packageName)}
                      onValueChange={(selected) =>
                        save({
                          packages: selected
                            ? [...status.packages, app.packageName]
                            : status.packages.filter((name) => name !== app.packageName),
                        })
                      }
                    />
                  </View>
                ))}
              </Card>
              <Text style={styles.body}>
                Notification text, caller identity, and phone numbers are never saved or sent to
                Digital Brain. Alert decisions stay on this phone.
              </Text>
            </>
          )}
        </Animated.ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  flex: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 16 },
  card: { padding: 18, gap: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  appRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 6 },
  volume: { gap: 10 },
  title: { fontSize: 18, fontWeight: '600', color: theme.colors.ink },
  body: { fontSize: 14, lineHeight: 21, color: theme.colors.mutedInk },
  error: { fontSize: 14, color: theme.colors.accentDeep },
  input: {
    padding: 14,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    color: theme.colors.ink,
    backgroundColor: theme.colors.card,
  },
});
