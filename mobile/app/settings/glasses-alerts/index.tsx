import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import {
  AppState,
  PermissionsAndroid,
  Platform,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { isGlassesAlertsAvailable } from '@/glassesAlerts/runtime';
import GlassesAlertsNative, {
  type GlassesAlertApp,
  type GlassesAlertStatus,
} from '@/modules/digital-brain-glasses-alerts/src';
import { theme } from '@/theme';

const emptyStatus: GlassesAlertStatus = {
  notificationAccessGranted: false,
  phoneStatePermissionGranted: false,
  phoneActivelyInUse: false,
  glassesAudioAvailable: false,
  glassesAudioDeviceName: null,
  settings: { enabled: false, selectedPackages: [], expectedAudioDeviceName: null },
};

export default function GlassesAlertsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showError, showSuccess } = useAppNotice();
  const [status, setStatus] = React.useState<GlassesAlertStatus>(emptyStatus);
  const [apps, setApps] = React.useState<GlassesAlertApp[]>([]);
  const [search, setSearch] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);

  const refresh = React.useCallback(async () => {
    if (!GlassesAlertsNative || Platform.OS !== 'android') {
      setLoading(false);
      return;
    }
    try {
      const [nextStatus, nextApps] = await Promise.all([
        GlassesAlertsNative.getStatus(),
        GlassesAlertsNative.getLaunchableApps(),
      ]);
      setStatus(nextStatus);
      setApps(nextApps);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not load glasses alert settings.');
    } finally {
      setLoading(false);
    }
  }, [showError]);

  React.useEffect(() => {
    void refresh();
    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') void refresh();
    });
    return () => subscription.remove();
  }, [refresh]);

  const save = React.useCallback(
    async (enabled: boolean, packages = status.settings.selectedPackages) => {
      if (!GlassesAlertsNative) return;
      setSaving(true);
      try {
        const settings = await GlassesAlertsNative.saveSettings(enabled, packages);
        setStatus((current) => ({ ...current, settings }));
      } catch (error) {
        showError(error instanceof Error ? error.message : 'Could not save glasses alerts.');
      } finally {
        setSaving(false);
      }
    },
    [showError, status.settings.selectedPackages],
  );

  const openNotificationAccess = async () => {
    if (!GlassesAlertsNative) return;
    try {
      await GlassesAlertsNative.openNotificationAccessSettings();
      showSuccess('Allow Digital Brain notification access, then return here.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not open notification access settings.');
    }
  };

  const requestPhoneState = async () => {
    const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE, {
      title: 'Allow incoming-call alerts',
      message:
        'Digital Brain checks only whether the phone is ringing so it can alert your connected glasses. It does not read or save phone numbers.',
      buttonPositive: 'Allow',
      buttonNegative: 'Not now',
    });
    if (result === PermissionsAndroid.RESULTS.GRANTED) {
      await GlassesAlertsNative?.refreshNotificationListener();
      showSuccess('Incoming-call alerts are allowed.');
      await refresh();
    }
  };

  const togglePackage = async (packageName: string) => {
    const selected = new Set(status.settings.selectedPackages);
    if (selected.has(packageName)) selected.delete(packageName);
    else selected.add(packageName);
    await save(status.settings.enabled, [...selected]);
  };

  const testAlert = async (call: boolean) => {
    if (!GlassesAlertsNative) return;
    const played = call
      ? await GlassesAlertsNative.playTestCallAlert()
      : await GlassesAlertsNative.playTestAlert();
    if (!played) {
      showError('Connect and audio-pair your Mentra Live before testing an alert.');
    }
  };

  if (Platform.OS !== 'android') {
    return (
      <View style={styles.screen}>
        <View style={[styles.centered, { paddingTop: insets.top + 24 }]}>
          <Ionicons name="phone-portrait-outline" size={32} color={theme.colors.teal} />
          <Text style={styles.title}>Glasses alerts are Android-only</Text>
          <Text style={styles.subtitle}>Android notification access is required to filter other apps.</Text>
          <Button label="Back" onPress={() => router.back()} style={styles.button} />
        </View>
      </View>
    );
  }

  const selected = new Set(status.settings.selectedPackages);
  const query = search.trim().toLowerCase();
  const visibleApps = apps.filter(
    (app) => !query || app.label.toLowerCase().includes(query) || app.packageName.toLowerCase().includes(query),
  );
  const configured =
    status.settings.enabled &&
    status.notificationAccessGranted &&
    status.phoneStatePermissionGranted &&
    selected.size > 0;

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 28 },
        ]}
        keyboardShouldPersistTaps="handled"
      >
        <Pressable style={styles.back} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={theme.colors.ink} />
          <Text style={styles.backText}>Smart glasses</Text>
        </Pressable>

        <View style={styles.hero}>
          <View style={styles.heroIcon}>
            <Ionicons name="notifications-outline" size={28} color={theme.colors.teal} />
          </View>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>SMART GLASSES</Text>
            <Text style={styles.title}>Glasses alerts</Text>
            <Text style={styles.subtitle}>
              Hear a subtle chime for selected apps and a repeating ring for incoming calls.
            </Text>
          </View>
        </View>

        {!isGlassesAlertsAvailable() ? (
          <Text style={styles.warning}>This build needs to be rebuilt to enable glasses alerts.</Text>
        ) : null}

        <Card style={styles.card}>
          <View style={styles.row}>
            <View style={styles.rowCopy}>
              <Text style={styles.cardTitle}>Glasses alerts</Text>
              <Text style={styles.sectionSubtitle}>
                {configured ? 'Ready for selected apps and incoming calls.' : 'Finish the setup below to start alerting.'}
              </Text>
            </View>
            <Switch
              value={status.settings.enabled}
              onValueChange={(value) => void save(value)}
              disabled={saving || loading || !GlassesAlertsNative}
              trackColor={{ false: '#d7d0c7', true: theme.colors.teal }}
            />
          </View>
        </Card>

        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Required access</Text>
          <Text style={styles.sectionSubtitle}>
            The phone keeps its normal notification sounds. Glasses alerts run only while the phone is locked or inactive, so they stay quiet while you are using it.
          </Text>
          <PermissionRow
            icon="notifications-outline"
            title="Notification access"
            detail="Filters only the app that sent a notification. Titles and messages are never saved."
            granted={status.notificationAccessGranted}
            label="Open settings"
            onPress={() => void openNotificationAccess()}
          />
          <PermissionRow
            icon="call-outline"
            title="Incoming calls"
            detail="Checks only whether the phone is ringing, then repeats a distinct glasses ring until the call changes state."
            granted={status.phoneStatePermissionGranted}
            label="Allow calls"
            onPress={() => void requestPhoneState()}
          />
          <View style={styles.routeRow}>
            <Ionicons name="volume-high-outline" size={20} color={theme.colors.teal} />
            <View style={styles.rowCopy}>
              <Text style={styles.permissionTitle}>Glasses audio route</Text>
              <Text style={styles.permissionDetail}>
                {status.glassesAudioAvailable
                  ? `Ready: ${status.glassesAudioDeviceName ?? 'Mentra Live'}`
                  : `Audio-pair your ${status.settings.expectedAudioDeviceName ?? 'Mentra Live'} to test alerts.`}
              </Text>
            </View>
            <Ionicons
              name={status.glassesAudioAvailable ? 'checkmark-circle' : 'ellipse-outline'}
              size={22}
              color={status.glassesAudioAvailable ? theme.colors.teal : theme.colors.mutedInk}
            />
          </View>
          <View style={styles.routeRow}>
            <Ionicons name="phone-portrait-outline" size={20} color={theme.colors.teal} />
            <View style={styles.rowCopy}>
              <Text style={styles.permissionTitle}>Phone-use suppression</Text>
              <Text style={styles.permissionDetail}>
                {status.phoneActivelyInUse
                  ? 'Alerts are currently suppressed because this phone is awake and unlocked.'
                  : 'Alerts can play when the phone is locked or its screen is inactive.'}
              </Text>
            </View>
          </View>
          <View style={styles.testRow}>
            <Button
              label="Test app chime"
              variant="secondary"
              onPress={() => void testAlert(false)}
              disabled={!GlassesAlertsNative}
              style={styles.testButton}
            />
            <Button
              label="Test call ring"
              variant="secondary"
              onPress={() => void testAlert(true)}
              disabled={!GlassesAlertsNative}
              style={styles.testButton}
            />
          </View>
        </Card>

        <Card style={styles.card}>
          <View style={styles.appsHeading}>
            <View style={styles.rowCopy}>
              <Text style={styles.cardTitle}>Apps that can alert</Text>
              <Text style={styles.sectionSubtitle}>
                Choose an allow-list. Other app notifications are ignored by Digital Brain.
              </Text>
            </View>
            <Text style={styles.count}>{selected.size} selected</Text>
          </View>
          <View style={styles.searchField}>
            <Ionicons name="search-outline" size={18} color={theme.colors.mutedInk} />
            <TextInput
              value={search}
              onChangeText={setSearch}
              placeholder="Search installed apps"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.searchInput}
              autoCapitalize="none"
              autoCorrect={false}
            />
          </View>
          {loading ? <Text style={styles.loading}>Loading installed apps…</Text> : null}
          {!loading && visibleApps.length === 0 ? (
            <Text style={styles.loading}>No launchable apps match this search.</Text>
          ) : null}
          {visibleApps.map((app) => {
            const isSelected = selected.has(app.packageName);
            return (
              <Pressable
                key={app.packageName}
                style={styles.appRow}
                onPress={() => void togglePackage(app.packageName)}
                disabled={saving || !GlassesAlertsNative}
              >
                <View style={[styles.appIcon, isSelected && styles.appIconSelected]}>
                  <Ionicons
                    name={isSelected ? 'checkmark' : 'apps-outline'}
                    size={18}
                    color={isSelected ? '#fff' : theme.colors.teal}
                  />
                </View>
                <View style={styles.rowCopy}>
                  <Text style={styles.permissionTitle}>{app.label}</Text>
                  <Text style={styles.packageName}>{app.packageName}</Text>
                </View>
              </Pressable>
            );
          })}
        </Card>

        <View style={styles.privacyHint}>
          <Ionicons name="shield-checkmark-outline" size={19} color={theme.colors.teal} />
          <Text style={styles.privacyText}>
            Notification text, caller identity, and phone numbers never enter Digital Brain. The alert decision stays on this phone.
          </Text>
        </View>
      </ScrollView>
    </View>
  );
}

function PermissionRow({
  icon,
  title,
  detail,
  granted,
  label,
  onPress,
}: {
  icon: React.ComponentProps<typeof Ionicons>['name'];
  title: string;
  detail: string;
  granted: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <View style={styles.permissionRow}>
      <Ionicons name={icon} size={20} color={theme.colors.teal} />
      <View style={styles.rowCopy}>
        <Text style={styles.permissionTitle}>{title}</Text>
        <Text style={styles.permissionDetail}>{detail}</Text>
      </View>
      {granted ? (
        <Ionicons name="checkmark-circle" size={23} color={theme.colors.teal} />
      ) : (
        <Pressable style={styles.permissionButton} onPress={onPress}>
          <Text style={styles.permissionButtonText}>{label}</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  content: { paddingHorizontal: 20, gap: 14 },
  centered: { alignItems: 'center', paddingHorizontal: 28, gap: 14 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 8, alignSelf: 'flex-start' },
  backText: { color: theme.colors.ink, fontSize: 15, fontWeight: '600' },
  hero: { flexDirection: 'row', gap: 14, alignItems: 'flex-start', marginBottom: 4 },
  heroIcon: {
    width: 52,
    height: 52,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#dceeed',
  },
  heroCopy: { flex: 1, gap: 3 },
  eyebrow: { color: theme.colors.teal, fontSize: 11, fontWeight: '800', letterSpacing: 1.1 },
  title: { color: theme.colors.ink, fontSize: 27, fontWeight: '800', lineHeight: 32 },
  subtitle: { color: theme.colors.mutedInk, fontSize: 15, lineHeight: 21 },
  warning: {
    color: '#88453d',
    backgroundColor: '#f9e7e2',
    borderRadius: 12,
    padding: 12,
    fontSize: 14,
    lineHeight: 20,
  },
  card: { gap: 13, padding: 16 },
  row: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  rowCopy: { flex: 1, gap: 2 },
  cardTitle: { color: theme.colors.ink, fontSize: 17, fontWeight: '800' },
  sectionSubtitle: { color: theme.colors.mutedInk, fontSize: 13, lineHeight: 19 },
  permissionRow: { flexDirection: 'row', gap: 11, alignItems: 'center', paddingTop: 5 },
  permissionTitle: { color: theme.colors.ink, fontSize: 14, fontWeight: '700' },
  permissionDetail: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 17 },
  permissionButton: { backgroundColor: '#dceeed', paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10 },
  permissionButtonText: { color: theme.colors.teal, fontWeight: '800', fontSize: 12 },
  routeRow: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingTop: 5 },
  testRow: { flexDirection: 'row', gap: 10 },
  testButton: { flex: 1 },
  appsHeading: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  count: { color: theme.colors.teal, fontWeight: '800', fontSize: 12, marginTop: 3 },
  searchField: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: '#ddd5cb',
    borderRadius: 12,
    paddingHorizontal: 11,
    backgroundColor: '#fbf9f6',
  },
  searchInput: { flex: 1, color: theme.colors.ink, paddingVertical: 10, fontSize: 14 },
  loading: { color: theme.colors.mutedInk, fontSize: 13, paddingVertical: 8 },
  appRow: { flexDirection: 'row', gap: 11, alignItems: 'center', paddingVertical: 8 },
  appIcon: {
    width: 34,
    height: 34,
    borderRadius: 11,
    backgroundColor: '#eaf3f2',
    alignItems: 'center',
    justifyContent: 'center',
  },
  appIconSelected: { backgroundColor: theme.colors.teal },
  packageName: { color: theme.colors.mutedInk, fontSize: 11, lineHeight: 16 },
  privacyHint: { flexDirection: 'row', gap: 9, padding: 12, alignItems: 'flex-start' },
  privacyText: { flex: 1, color: theme.colors.mutedInk, fontSize: 12, lineHeight: 18 },
  button: { alignSelf: 'stretch', marginTop: 4 },
});
