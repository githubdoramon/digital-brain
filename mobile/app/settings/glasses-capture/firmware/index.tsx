import Ionicons from '@expo/vector-icons/Ionicons';
import { useRouter } from 'expo-router';
import React from 'react';
import { Alert, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  FirmwarePhase,
  getGlassesFirmwareState,
  initializeGlassesFirmware,
  installGlassesFirmware,
  refreshGlassesFirmware,
  subscribeGlassesFirmware,
} from '@/mentraCapture/firmware';
import { theme } from '@/theme';

export default function GlassesFirmwareScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [state, setState] = React.useState(getGlassesFirmwareState);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const run = React.useCallback(async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not check firmware.');
    } finally {
      setBusy(false);
    }
  }, []);

  React.useEffect(() => {
    const unsubscribe = subscribeGlassesFirmware(setState);
    void run(initializeGlassesFirmware);
    return unsubscribe;
  }, [run]);

  const updating = [
    FirmwarePhase.Starting,
    FirmwarePhase.Updating,
    FirmwarePhase.AwaitingStatus,
  ].includes(state.phase);
  const install = () =>
    Alert.alert(
      'Install glasses firmware?',
      'Install Mentra’s firmware matched to this app. This may include changing to a compatible version. Finish any video recording first, charge the glasses to at least 50%, and connect them to Wi-Fi with internet access. Keep them powered on and nearby; they may restart. Captures and wake commands pause during the update.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Install',
          onPress: () => {
            void run(installGlassesFirmware);
          },
        },
      ],
    );

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 32 },
      ]}
    >
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Back"
          onPress={() => router.back()}
          style={styles.back}
        >
          <Ionicons name="arrow-back" size={23} color={theme.colors.ink} />
        </Pressable>
        <Text style={styles.title}>Glasses firmware</Text>
      </View>
      <Text style={styles.subtitle}>Update Mentra Live directly from Digital Brain.</Text>
      <Card style={styles.card}>
        <Text style={styles.heading}>{updating ? 'Update in progress' : 'Firmware status'}</Text>
        <Text style={styles.body} accessibilityLiveRegion="polite">
          {state.detail}
        </Text>
        {updating ? (
          <View>
            <View
              style={styles.track}
              accessibilityRole="progressbar"
              accessibilityValue={{ min: 0, max: 100, now: state.progress }}
            >
              <View style={[styles.progress, { width: `${state.progress}%` }]} />
            </View>
            <Text style={styles.body}>{Math.round(state.progress)}%</Text>
          </View>
        ) : null}
        {state.device ? (
          <View style={styles.versions}>
            <Text style={styles.body}>
              Glasses software: {state.device.appVersion || 'Unavailable'}
            </Text>
            <Text style={styles.body}>
              Camera firmware: {state.device.mtkVersion || 'Unavailable'}
            </Text>
            <Text style={styles.body}>
              Bluetooth firmware: {state.device.besVersion || 'Unavailable'}
            </Text>
            <Text style={styles.body}>
              Battery:{' '}
              {state.device.batteryLevel >= 0 ? `${state.device.batteryLevel}%` : 'Unavailable'}
            </Text>
            <Text style={styles.body}>
              Glasses Wi-Fi: {state.device.wifiConnected ? 'Connected' : 'Disconnected'}
            </Text>
          </View>
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Button
          label={updating ? 'Refresh update status' : 'Check for updates'}
          loading={busy}
          disabled={busy}
          onPress={() => void run(refreshGlassesFirmware)}
          style={styles.button}
        />
        {state.phase === FirmwarePhase.Available ? (
          <Button
            label="Install firmware"
            disabled={busy}
            onPress={install}
            style={styles.button}
          />
        ) : null}
      </Card>
      <Text style={styles.body}>
        The glasses download firmware over their own Wi-Fi connection. You can leave this screen
        while the update runs. A restart announcement can be part of installation; progress must
        report completion before the update is considered finished.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  content: { paddingHorizontal: 20, gap: 18 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  back: { padding: 10, borderRadius: 24, backgroundColor: theme.colors.card },
  title: { fontSize: 26, fontWeight: '700', color: theme.colors.ink, flex: 1 },
  subtitle: { fontSize: 16, color: theme.colors.mutedInk },
  card: { padding: 20, gap: 14 },
  heading: { fontSize: 19, fontWeight: '700', color: theme.colors.ink },
  body: { fontSize: 15, lineHeight: 22, color: theme.colors.mutedInk },
  versions: { gap: 6, paddingVertical: 8 },
  button: { marginTop: 8 },
  error: { color: theme.colors.accentDeep, fontSize: 15, lineHeight: 22 },
  track: { height: 8, borderRadius: 4, backgroundColor: theme.colors.paleTeal, overflow: 'hidden' },
  progress: { height: 8, backgroundColor: theme.colors.teal },
});
