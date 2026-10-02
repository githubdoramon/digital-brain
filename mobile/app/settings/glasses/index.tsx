import { useFocusEffect } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Alert,
  Animated,
  AppState,
  KeyboardAvoidingView,
  Platform,
  Share,
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
import {
  glassesNative,
  requestGlassesPermissions,
  requestGlassesWifiPermission,
} from '@/glasses/runtime';
import { useAppNotice } from '@/hooks/useAppNotice';
import type { GlassesStatus, GlassesWifiNetwork } from '@/modules/digital-brain-runtime/src';
import { theme } from '@/theme';

export default function GlassesSettingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { showError, showSuccess } = useAppNotice();
  const [status, setStatus] = React.useState<GlassesStatus | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [wifiOpen, setWifiOpen] = React.useState(false);
  const [ssid, setSsid] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [wifiNetworks, setWifiNetworks] = React.useState<GlassesWifiNetwork[]>([]);
  const [wifiScanState, setWifiScanState] = React.useState<
    'idle' | 'scanning' | 'complete' | 'error'
  >('idle');
  const [manualWifi, setManualWifi] = React.useState(false);
  const wifiScanRequest = React.useRef(0);

  React.useEffect(
    () => () => {
      wifiScanRequest.current += 1;
    },
    [],
  );

  const refresh = React.useCallback(async () => {
    try {
      setStatus(await glassesNative().getGlassesStatus());
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Could not read glasses state.');
    }
  }, []);

  useFocusEffect(
    React.useCallback(() => {
      let disposed = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const poll = async () => {
        if (AppState.currentState !== 'active' || disposed) return;
        await refresh();
        if (!disposed) timer = setTimeout(() => void poll(), 1500);
      };
      void poll();
      const listener = AppState.addEventListener('change', (state) => {
        if (timer) clearTimeout(timer);
        if (state === 'active') void poll();
      });
      return () => {
        disposed = true;
        if (timer) clearTimeout(timer);
        listener.remove();
      };
    }, [refresh]),
  );

  const run = async (action: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Glasses operation failed.');
    } finally {
      setBusy(false);
    }
  };
  const connected = status?.ready === true;
  React.useEffect(() => {
    if (!connected) {
      wifiScanRequest.current += 1;
      setWifiOpen(false);
      setWifiNetworks([]);
      setWifiScanState('idle');
      setSsid('');
      setPassword('');
    }
  }, [connected]);
  const scanWifi = () =>
    void run(async () => {
      const request = ++wifiScanRequest.current;
      setWifiScanState('scanning');
      setWifiNetworks([]);
      setSsid('');
      setPassword('');
      setManualWifi(false);
      try {
        const native = glassesNative();
        if (!native.scanGlassesWifi) {
          throw new Error('Install a new Android native build to scan glasses Wi-Fi.');
        }
        const networks = await native.scanGlassesWifi();
        if (request !== wifiScanRequest.current) return;
        setWifiNetworks(networks);
        setWifiScanState('complete');
      } catch (error) {
        if (request !== wifiScanRequest.current) return;
        setWifiScanState('error');
        throw error;
      }
    });
  const selectedWifi = wifiNetworks.find((network) => network.ssid === ssid);
  const requiresPassword = manualWifi || selectedWifi?.requiresPassword !== false;
  const disabled = busy || status?.updateActive === true;
  const enable = (value: boolean) =>
    void run(async () => {
      if (value) await requestGlassesPermissions();
      await glassesNative().setGlassesEnabled(value);
    });
  const update = () =>
    Alert.alert(
      'Update glasses?',
      'Keep your glasses powered and connected to Wi-Fi. They may restart during installation.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Update', onPress: () => void run(() => glassesNative().updateGlassesFirmware()) },
      ],
    );
  const forget = () =>
    Alert.alert(
      'Forget glasses?',
      'Remove the saved pair from Digital Brain. You can pair again later.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Forget',
          style: 'destructive',
          onPress: () => void run(() => glassesNative().forgetGlasses()),
        },
      ],
    );

  return (
    <View style={styles.screen}>
      <LinearGradient colors={theme.gradients.sunrise} style={StyleSheet.absoluteFill} />
      <CollapsingTopBar
        title="Glasses"
        secondaryTitle="Mentra Live"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <Animated.ScrollView
          automaticallyAdjustKeyboardInsets
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
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
              paddingBottom: insets.bottom + 40,
            },
          ]}
        >
          {loadError && (
            <Card style={styles.card}>
              <Text style={styles.error}>{loadError}</Text>
              <Button label="Refresh" variant="secondary" onPress={() => void refresh()} />
            </Card>
          )}
          {status && (
            <>
              <Card style={styles.card}>
                <View style={styles.row}>
                  <View style={styles.flex}>
                    <Text style={styles.title}>Enable Glasses</Text>
                    <Text style={styles.body}>
                      Maintain connection in the background and reconnect after restarting your
                      phone.
                    </Text>
                  </View>
                  <Switch
                    accessibilityLabel="Enable Glasses"
                    value={status.enabled}
                    disabled={disabled}
                    onValueChange={enable}
                    trackColor={{ true: theme.colors.teal }}
                  />
                </View>
                <Text style={styles.state}>
                  {!status.enabled
                    ? 'Disabled'
                    : !status.permitted
                      ? 'Permission required'
                      : !status.bluetoothOn
                        ? 'Bluetooth is off'
                        : connected
                          ? 'Connected and ready'
                          : status.connection === 'connected'
                            ? 'Connected · preparing glasses'
                            : status.connection === 'connecting'
                              ? 'Connecting…'
                              : status.nextRetryAtMs
                                ? 'Waiting to reconnect'
                                : status.running
                                  ? 'Ready to pair'
                                  : 'Runtime stopped'}
                </Text>
                <Text style={styles.body}>{status.savedName ?? 'No saved glasses'}</Text>
                {status.nextRetryAtMs && (
                  <Text style={styles.body}>
                    Next attempt around {new Date(status.nextRetryAtMs).toLocaleTimeString()}
                  </Text>
                )}
                {status.error && <Text style={styles.error}>{status.error}</Text>}
                {status.enabled && !status.permitted && (
                  <Button label="Grant permissions" disabled={busy} onPress={() => enable(true)} />
                )}
                {status.enabled && status.permitted && !status.running && (
                  <Button label="Restart connection" disabled={busy} onPress={() => enable(true)} />
                )}
                {status.battery != null && connected && (
                  <Text style={styles.body}>Glasses battery: {status.battery}%</Text>
                )}
                {status.enabled && status.savedName && (
                  <Button
                    label={connected ? 'Disconnect' : 'Connect now'}
                    variant="secondary"
                    disabled={disabled || status.connection === 'connecting'}
                    onPress={() =>
                      connected ? enable(false) : void run(() => glassesNative().connectGlasses())
                    }
                  />
                )}
                {status.savedName && (
                  <Button
                    label="Forget glasses"
                    variant="clear"
                    disabled={disabled}
                    onPress={forget}
                  />
                )}
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Pair glasses</Text>
                <Text style={styles.body}>
                  Put Mentra Live in pairing mode, then scan. Accept any Android pairing prompts.
                </Text>
                <Button
                  label={status.scanning ? 'Scanning…' : 'Scan for glasses'}
                  loading={status.scanning}
                  disabled={disabled || !status.enabled || !status.running || !status.bluetoothOn}
                  onPress={() => void run(() => glassesNative().scanGlasses())}
                />
                {status.devices.map((device) => (
                  <Button
                    key={device.id}
                    label={device.name}
                    variant="secondary"
                    disabled={disabled}
                    onPress={() => void run(() => glassesNative().selectGlasses(device.id))}
                  />
                ))}
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Photos and videos</Text>
                <Text style={styles.body}>
                  {status.media?.pending ?? 0} pending ·{' '}
                  {status.media?.status ?? 'Waiting for glasses'}
                </Text>
                <Text style={styles.body}>
                  Originals sync automatically over Wi-Fi, using the glasses hotspot when needed.
                  Uploads can use cellular.
                </Text>
                <Button
                  label="Sync now"
                  disabled={disabled || !status.enabled}
                  onPress={() =>
                    void run(async () => {
                      await requestGlassesWifiPermission();
                      await glassesNative().syncGlassesMedia();
                    })
                  }
                />
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Firmware</Text>
                <Text style={styles.body}>
                  {status.firmwareSource === 'APP'
                    ? 'Glasses app'
                    : status.firmwareSource === 'BES'
                      ? 'BES firmware'
                      : status.firmwareSource === 'MTK'
                        ? 'MTK firmware'
                        : 'Firmware'}
                  : {status.firmwareVersion ?? 'Connect to read version'}
                </Text>
                {status.appVersion && status.firmwareSource !== 'APP' && (
                  <Text style={styles.body}>Glasses app: {status.appVersion}</Text>
                )}
                <Text style={status.updateAvailable ? styles.state : styles.body}>
                  {status.updateActive
                    ? `Updating · ${status.updateProgress}% · ${status.updateStatus}`
                    : status.firmwareBusy
                      ? 'Checking for updates…'
                      : status.updateAvailable
                        ? 'Update available'
                        : status.updateStatus === 'up_to_date'
                          ? 'Up to date'
                          : 'Updates are checked when glasses connect.'}
                </Text>
                {status.firmwareError && <Text style={styles.error}>{status.firmwareError}</Text>}
                <Button
                  label="Check for update"
                  variant="secondary"
                  disabled={busy || !connected || status.firmwareBusy}
                  onPress={() => void run(() => glassesNative().checkGlassesFirmware())}
                />
                {status.updateAvailable && (
                  <Button
                    label="Update firmware"
                    disabled={disabled || !connected || status.firmwareBusy}
                    onPress={update}
                  />
                )}
                <Button
                  label={wifiOpen ? 'Close Wi-Fi setup' : 'Set glasses Wi-Fi'}
                  variant="clear"
                  disabled={disabled || !connected}
                  onPress={() => {
                    setWifiOpen(!wifiOpen);
                    setPassword('');
                    if (!wifiOpen) {
                      setSsid('');
                      setManualWifi(false);
                      scanWifi();
                    }
                  }}
                />
                {wifiOpen && (
                  <>
                    <Text style={styles.body}>
                      The glasses need internet access to install updates.
                    </Text>
                    <Button
                      label={wifiScanState === 'scanning' ? 'Scanning for networks…' : 'Scan again'}
                      variant="secondary"
                      loading={wifiScanState === 'scanning'}
                      disabled={disabled || !connected}
                      onPress={scanWifi}
                    />
                    {wifiScanState === 'complete' && wifiNetworks.length === 0 && (
                      <Text style={styles.body}>
                        No networks found. Try scanning again or enter a hidden network.
                      </Text>
                    )}
                    {wifiScanState === 'error' && (
                      <Text style={styles.body}>
                        Could not scan networks. Try again with the glasses connected.
                      </Text>
                    )}
                    {wifiNetworks.map((network) => (
                      <Button
                        key={network.ssid}
                        label={`${!manualWifi && ssid === network.ssid ? '✓ ' : ''}${network.ssid} · ${network.requiresPassword ? 'Password required' : 'Open'}`}
                        variant={!manualWifi && ssid === network.ssid ? 'primary' : 'secondary'}
                        disabled={disabled || !connected}
                        onPress={() => {
                          setSsid(network.ssid);
                          setPassword('');
                          setManualWifi(false);
                        }}
                      />
                    ))}
                    <Button
                      label={manualWifi ? 'Choose an available network' : 'Enter a hidden network'}
                      variant="clear"
                      disabled={disabled}
                      onPress={() => {
                        setManualWifi(!manualWifi);
                        setSsid('');
                        setPassword('');
                      }}
                    />
                    {manualWifi && (
                      <TextInput
                        accessibilityLabel="Wi-Fi network name"
                        placeholder="Wi-Fi network name"
                        value={ssid}
                        onChangeText={setSsid}
                        autoCapitalize="none"
                        autoCorrect={false}
                        style={styles.input}
                        editable={!disabled}
                      />
                    )}
                    {!!ssid && requiresPassword && (
                      <TextInput
                        accessibilityLabel="Wi-Fi password"
                        placeholder="Wi-Fi password"
                        value={password}
                        onChangeText={setPassword}
                        secureTextEntry
                        autoCapitalize="none"
                        autoCorrect={false}
                        style={styles.input}
                        editable={!disabled}
                      />
                    )}
                    <Button
                      label="Connect glasses to Wi-Fi"
                      disabled={disabled || !ssid.trim() || !connected}
                      onPress={() =>
                        void run(async () => {
                          await glassesNative().configureGlassesWifi(
                            manualWifi ? ssid.trim() : ssid,
                            requiresPassword ? password : '',
                          );
                          setPassword('');
                          setWifiOpen(false);
                          showSuccess('Glasses Wi-Fi connected.');
                        })
                      }
                    />
                  </>
                )}
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Alerts</Text>
                <Text style={styles.body}>
                  Notification chimes for selected apps and repeating incoming-call alerts.
                </Text>
                <Button
                  label="Glasses alerts"
                  variant="secondary"
                  onPress={() => router.push('/settings/glasses/alerts')}
                />
              </Card>
              <Card style={styles.card}>
                <Text style={styles.title}>Connection diagnostics</Text>
                <Text style={styles.body}>SDK 3.1.1 · reconnect delay capped at 5 minutes.</Text>
                <Text style={styles.body}>
                  Battery and awake counters describe the whole phone. CPU time describes this app
                  process.
                </Text>
                <Button
                  label="Export diagnostics"
                  variant="secondary"
                  disabled={busy}
                  onPress={() =>
                    void run(async () => {
                      const diagnostics = await glassesNative().getGlassesDiagnostics();
                      await Share.share({
                        message: JSON.stringify(diagnostics, null, 2),
                        title: 'Glasses diagnostics',
                      });
                    })
                  }
                />
              </Card>
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
  row: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  title: { fontSize: 18, fontWeight: '600', color: theme.colors.ink },
  body: { fontSize: 14, lineHeight: 21, color: theme.colors.mutedInk },
  state: { fontSize: 16, fontWeight: '600', color: theme.colors.teal },
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
