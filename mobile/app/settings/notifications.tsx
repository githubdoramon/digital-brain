import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React, { useCallback, useMemo, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import {
  ActivityIndicator,
  Alert,
  Animated,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import {
  getDeviceRegistrationIfGranted,
  registerForPushNotifications,
} from '@/notifications/register';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type NotificationChannel = 'push' | 'email';

type NotificationTypeSetting = {
  notificationType: string;
  title: string;
  enabled: boolean;
  channels: NotificationChannel[];
};

type NotificationSettingsResponse = {
  pushAvailable: boolean;
  types: NotificationTypeSetting[];
};

const TOKEN_KEY = 'digitalbrain.expoPushToken';
const CHANNELS: NotificationChannel[] = ['push', 'email'];

function formatChannels(channels: NotificationChannel[]): string {
  if (!channels.length) return 'Disabled';
  return channels.map((channel) => channel[0].toUpperCase() + channel.slice(1)).join(' + ');
}

export default function NotificationSettingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { token, signOut } = useAuth();
  const { showSuccess, showError, showWarning } = useAppNotice();
  const scrollY = React.useRef(new Animated.Value(0)).current;

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [settings, setSettings] = useState<NotificationTypeSetting[]>([]);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [activeTypeId, setActiveTypeId] = useState<string | null>(null);
  const [draftChannels, setDraftChannels] = useState<Set<NotificationChannel>>(new Set());
  const [savingTypeId, setSavingTypeId] = useState<string | null>(null);
  const [pushSetupRequired, setPushSetupRequired] = useState(false);

  const activeSetting = useMemo(
    () => settings.find((item) => item.notificationType === activeTypeId) ?? null,
    [activeTypeId, settings],
  );

  const syncCurrentDevicePushRegistration = useCallback(async () => {
    if (!token) {
      return false;
    }

    const registration = await getDeviceRegistrationIfGranted();
    if (!registration) {
      await SecureStore.deleteItemAsync(TOKEN_KEY);
      return false;
    }

    await apiFetch('/mobile/devices/register', {
      method: 'POST',
      body: JSON.stringify(registration),
      token,
    });
    await SecureStore.setItemAsync(TOKEN_KEY, registration.expoPushToken);
    return true;
  }, [token]);

  const loadSettings = useCallback(async () => {
    if (!token) {
      setIsLoading(false);
      setPushSetupRequired(false);
      return;
    }
    setIsLoading(true);
    try {
      const response = (await apiFetch('/mobile/settings/notifications', { token })) as NotificationSettingsResponse;
      const nextSettings = Array.isArray(response?.types) ? response.types : [];
      setSettings(nextSettings);

      const hasPushEnabled = nextSettings.some((item) => item.channels.includes('push'));
      if (!hasPushEnabled) {
        await SecureStore.deleteItemAsync(TOKEN_KEY);
        setPushSetupRequired(false);
        return;
      }

      const pushReadyOnThisInstall = await syncCurrentDevicePushRegistration();
      setPushSetupRequired(!pushReadyOnThisInstall);
    } catch (error) {
      const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
      if (authExpired) {
        await signOut();
        Alert.alert('Session expired', 'Please sign in again.');
        return;
      }
      showError('Could not load settings. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }, [showError, signOut, syncCurrentDevicePushRegistration, token]);

  useFocusEffect(
    useCallback(() => {
      void loadSettings();
    }, [loadSettings]),
  );

  const unregisterDevice = useCallback(async () => {
    const existing = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!existing) return;
    await apiFetch(`/mobile/devices/unregister?expoPushToken=${encodeURIComponent(existing)}`, {
      method: 'DELETE',
      token,
    });
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  }, [token]);

  const persistChannels = useCallback(
    async (notificationType: string, channels: NotificationChannel[]) => {
      if (!token) return;
      const normalizedChannels = [...new Set(channels)];

      const hasPush = normalizedChannels.includes('push');
      if (hasPush) {
        let registration = await getDeviceRegistrationIfGranted();
        if (!registration) {
          registration = await registerForPushNotifications();
        }
        if (!registration) {
          Alert.alert(
            'Push permission required',
            'We could not enable push notifications without system permission.',
          );
          const fallbackChannels = normalizedChannels.filter((channel) => channel !== 'push');
          if (!fallbackChannels.length) {
            await persistChannels(notificationType, []);
            return;
          }
          await persistChannels(notificationType, fallbackChannels);
          return;
        }
        await apiFetch('/mobile/devices/register', {
          method: 'POST',
          body: JSON.stringify(registration),
          token,
        });
        await SecureStore.setItemAsync(TOKEN_KEY, registration.expoPushToken);
        setPushSetupRequired(false);
      }

      const updated = (await apiFetch(`/mobile/settings/notifications/${notificationType}`, {
        method: 'PUT',
        body: JSON.stringify({ channels: normalizedChannels }),
        token,
      })) as NotificationTypeSetting;

      const nextSettings = settings.map((item) =>
        item.notificationType === notificationType ? updated : item,
      );
      setSettings(nextSettings);

      const pushStillEnabled = nextSettings.some((item) => item.channels.includes('push'));
      if (!pushStillEnabled) {
        await unregisterDevice();
        setPushSetupRequired(false);
      }
      showSuccess(
        normalizedChannels.length
          ? 'Notification settings updated.'
          : 'Notifications disabled for this type.',
      );
    },
    [settings, showSuccess, token, unregisterDevice],
  );

  const handleToggle = useCallback(
    async (item: NotificationTypeSetting, enabled: boolean) => {
      if (isSaving) return;
      if (!enabled) {
        setIsSaving(true);
        setSavingTypeId(item.notificationType);
        try {
          await persistChannels(item.notificationType, []);
        } catch (error) {
          const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
          if (authExpired) {
            await signOut();
            Alert.alert('Session expired', 'Please sign in again.');
            return;
          }
          showError('We could not save notification settings.');
        } finally {
          setIsSaving(false);
          setSavingTypeId(null);
        }
        return;
      }

      setActiveTypeId(item.notificationType);
      setDraftChannels(new Set(item.channels));
      setSheetOpen(true);
    },
    [isSaving, persistChannels, showError, signOut],
  );

  const openTypeSheet = useCallback(
    (item: NotificationTypeSetting) => {
      if (isSaving) return;
      setActiveTypeId(item.notificationType);
      setDraftChannels(new Set(item.channels));
      setSheetOpen(true);
    },
    [isSaving],
  );

  const closeTypeSheet = useCallback(() => {
    setSheetOpen(false);
    setActiveTypeId(null);
    setDraftChannels(new Set());
  }, []);

  const handleApplyChannels = useCallback(async () => {
    if (!activeSetting || isSaving) return;
    const selectedChannels = Array.from(draftChannels);
    closeTypeSheet();
    setIsSaving(true);
    setSavingTypeId(activeSetting.notificationType);
    try {
      await persistChannels(activeSetting.notificationType, selectedChannels);
      if (!selectedChannels.length) {
        showWarning('Pick at least one channel to enable this notification type.');
      }
    } catch (error) {
      const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
      if (authExpired) {
        await signOut();
        Alert.alert('Session expired', 'Please sign in again.');
        return;
      }
      showError('We could not save notification settings.');
    } finally {
      setIsSaving(false);
      setSavingTypeId(null);
    }
  }, [activeSetting, closeTypeSheet, draftChannels, isSaving, persistChannels, showError, showWarning, signOut]);

  const toggleDraftChannel = useCallback((channel: NotificationChannel) => {
    setDraftChannels((prev) => {
      const next = new Set(prev);
      if (next.has(channel)) {
        next.delete(channel);
      } else {
        next.add(channel);
      }
      return next;
    });
  }, []);

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
        <Card style={styles.card}>
          <Text style={styles.helperText}>
            {pushSetupRequired
              ? 'Push was enabled before, but this install still needs OS permission and device registration. Open a notification type and re-enable Push on this device.'
              : 'Enable each notification type, then choose where you want to receive it.'}
          </Text>
        </Card>

        {isLoading ? (
          <Card style={[styles.card, styles.loadingCard]}>
            <ActivityIndicator color={theme.colors.accent} />
            <Text style={styles.loadingText}>Loading notifications...</Text>
          </Card>
        ) : (
          settings.map((item) => (
            <Card key={item.notificationType} style={[styles.card, styles.rowCard]}
            >
              <Pressable
                style={styles.rowPressable}
                onPress={() => openTypeSheet(item)}
                disabled={isSaving}
              >
                <View style={styles.rowTextWrap}>
                  <Text style={styles.rowTitle}>{item.title}</Text>
                  <Text style={styles.rowSubtitle}>
                    {item.channels.includes('push') && pushSetupRequired
                      ? `${formatChannels(item.channels)} - setup needed on this device`
                      : formatChannels(item.channels)}
                  </Text>
                </View>
                <View style={styles.rowActions}>
                  <Switch
                    value={item.enabled}
                    onValueChange={(value) => {
                      void handleToggle(item, value);
                    }}
                    trackColor={{ false: theme.colors.line, true: theme.colors.paleTeal }}
                    thumbColor={item.enabled ? theme.colors.accent : '#f2f2f2'}
                    disabled={isSaving}
                  />
                  <Ionicons name="chevron-forward" size={18} color={theme.colors.mutedInk} />
                </View>
                {savingTypeId === item.notificationType ? (
                  <View style={styles.rowSavingOverlay} pointerEvents="none">
                    <ActivityIndicator color={theme.colors.accentDeep} />
                  </View>
                ) : null}
              </Pressable>
            </Card>
          ))
        )}
      </Animated.ScrollView>

      <CollapsingTopBar
        title="Notifications"
        secondaryTitle="Type + channel control"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />

      <BottomSheet visible={sheetOpen} onClose={closeTypeSheet}>
        <Text style={styles.sheetTitle}>Delivery channels</Text>
        <Text style={styles.sheetSubtitle}>
          {activeSetting?.title || 'Notification'}
        </Text>

        <View style={styles.sheetOptions}>
          {CHANNELS.map((channel) => {
            const selected = draftChannels.has(channel);
            return (
              <Pressable
                key={channel}
                style={[styles.optionRow, selected && styles.optionRowSelected]}
                onPress={() => toggleDraftChannel(channel)}
              >
                <View style={styles.optionLabelWrap}>
                  <Text style={styles.optionTitle}>
                    {channel === 'push' ? 'Push' : 'Email'}
                  </Text>
                </View>
                <Ionicons
                  name={selected ? 'checkbox' : 'square-outline'}
                  size={22}
                  color={selected ? theme.colors.accentDeep : theme.colors.mutedInk}
                />
              </Pressable>
            );
          })}
        </View>

        <View style={styles.sheetButtons}>
          <Button
            label="Cancel"
            variant="secondary"
            onPress={() => {
              closeTypeSheet();
            }}
            style={styles.sheetButton}
          />
          <Button
            label="Apply"
            onPress={() => {
              void handleApplyChannels();
            }}
            style={styles.sheetButton}
          />
        </View>
      </BottomSheet>
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
    padding: 18,
    marginTop: 14,
  },
  helperText: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 18,
  },
  loadingCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  loadingText: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  rowCard: {
    padding: 0,
    overflow: 'hidden',
  },
  rowPressable: {
    minHeight: 84,
    paddingHorizontal: 18,
    paddingVertical: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  rowTextWrap: {
    flex: 1,
  },
  rowActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  rowSavingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(255,255,255,0.3)',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: theme.radius.xl,
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
  sheetTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  sheetSubtitle: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  sheetOptions: {
    gap: 10,
    marginTop: 6,
  },
  optionRow: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  optionRowSelected: {
    borderColor: theme.colors.accentDeep,
    backgroundColor: 'rgba(47, 111, 116, 0.08)',
  },
  optionLabelWrap: {
    flex: 1,
  },
  optionTitle: {
    fontSize: 15,
    color: theme.colors.ink,
    fontWeight: '600',
  },
  sheetButtons: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 6,
  },
  sheetButton: {
    flex: 1,
  },
});
