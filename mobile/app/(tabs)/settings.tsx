import { LinearGradient } from 'expo-linear-gradient';
import React, { useCallback, useEffect, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { ActivityIndicator, Alert, StyleSheet, Switch, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  getDeviceRegistrationIfGranted,
  registerForPushNotifications,
} from '@/notifications/register';
import { theme } from '@/theme';

type SettingsResponse = {
  pushNotificationsEnabled: boolean;
};

const TOKEN_KEY = 'digitalbrain.expoPushToken';

export default function SettingsScreen() {
  const { token, signOut } = useAuth();
  const insets = useSafeAreaInsets();
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [pushEnabled, setPushEnabled] = useState(false);

  const unregisterDevice = useCallback(async () => {
    const existing = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!existing) return;
    await apiFetch(`/mobile/devices/unregister?expoPushToken=${encodeURIComponent(existing)}`, {
      method: 'DELETE',
      token,
    });
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  }, [token]);

  const reconcilePushState = useCallback(
    async (backendEnabled: boolean) => {
      const registration = await getDeviceRegistrationIfGranted();
      if (registration && backendEnabled) {
        setPushEnabled(true);
        return;
      }
      if (!registration && !backendEnabled) {
        setPushEnabled(false);
        return;
      }
      if (registration && !backendEnabled) {
        try {
          await apiFetch('/mobile/settings/push-notifications', {
            method: 'PUT',
            body: JSON.stringify({ enabled: true }),
            token,
          });
          await apiFetch('/mobile/devices/register', {
            method: 'POST',
            body: JSON.stringify(registration),
            token,
          });
          await SecureStore.setItemAsync(TOKEN_KEY, registration.expoPushToken);
          setPushEnabled(true);
          return;
        } catch (error) {
          console.error('push reconciliation failed', error);
          // fall through to disable below
        }
      }

      if (backendEnabled && !registration) {
        try {
          await apiFetch('/mobile/settings/push-notifications', {
            method: 'PUT',
            body: JSON.stringify({ enabled: false }),
            token,
          });
        } catch (error) {
          console.error('failed to disable push settings', error);
          // noop: keep state if backend update fails
        }
        await unregisterDevice();
      }
      setPushEnabled(false);
    },
    [token, unregisterDevice],
  );

  useEffect(() => {
    let mounted = true;
    token && (async () => {
      try {
        const response = (await apiFetch('/mobile/settings', { token })) as SettingsResponse;
        if (mounted) {
          await reconcilePushState(Boolean(response?.pushNotificationsEnabled));
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [token, reconcilePushState]);

  const togglePush = async (value: boolean) => {
    const previous = pushEnabled;
    setPushEnabled(value);
    setIsSaving(true);
    try {
      if (value) {
        const registration = await registerForPushNotifications();
        if (!registration) {
          Alert.alert(
            'Notifications unavailable',
            'Enable notifications in system settings or use a physical device to register.',
          );
          setPushEnabled(false);
          return;
        }
        await apiFetch('/mobile/settings/push-notifications', {
          method: 'PUT',
          body: JSON.stringify({ enabled: true }),
          token,
        });
        await apiFetch('/mobile/devices/register', {
          method: 'POST',
          body: JSON.stringify(registration),
          token,
        });
        await SecureStore.setItemAsync(TOKEN_KEY, registration.expoPushToken);
        setPushEnabled(true);
      } else {
        await apiFetch('/mobile/settings/push-notifications', {
          method: 'PUT',
          body: JSON.stringify({ enabled: false }),
          token,
        });
        await unregisterDevice();
        setPushEnabled(false);
      }
    } catch (error) {
      const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
      if (authExpired) {
        await signOut();
        Alert.alert('Session expired', 'Please sign in again.');
        return;
      }
      console.error('togglePush error', error);
      Alert.alert('Update failed', 'We could not update push settings. Please try again.');
      setPushEnabled(previous);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <LinearGradient
      colors={theme.gradients.sunrise}
      style={[styles.container, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 24 }]}
    >
      <View style={styles.header}>
        <Text style={styles.kicker}>Configuration</Text>
        <Text style={styles.title}>Control your brain</Text>
        <Text style={styles.subtitle}>Personalize how the Digital Brain works for you.</Text>
      </View>

      <Card style={styles.card}>
        <View style={styles.row}>
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>Push notifications</Text>
            <Text style={styles.rowSubtitle}>
              Turn on quick alerts for new memories and follow-ups.
            </Text>
          </View>
          {isLoading ? (
            <ActivityIndicator color={theme.colors.accent} />
          ) : (
            <Switch
              value={pushEnabled}
              onValueChange={togglePush}
              trackColor={{ false: theme.colors.line, true: theme.colors.paleTeal }}
              thumbColor={pushEnabled ? theme.colors.accent : '#f2f2f2'}
              disabled={isSaving}
            />
          )}
        </View>
        {isSaving && <Text style={styles.saving}>Saving preference…</Text>}
      </Card>

      <Button
        label="Sign out"
        onPress={signOut}
        variant="primary"
        style={styles.signOutButton}
      />
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    paddingHorizontal: 24,
  },
  header: {
    marginBottom: 24,
  },
  kicker: {
    textTransform: 'uppercase',
    letterSpacing: 3,
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
    marginTop: 6,
  },
  subtitle: {
    marginTop: 6,
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  card: {
    borderRadius: theme.radius.xl,
    padding: 20,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
  },
  textBlock: {
    flex: 1,
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
  saving: {
    marginTop: 12,
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  signOutButton: {
    marginTop: 20,
    alignSelf: 'stretch',
    borderRadius: theme.radius.md,
    paddingHorizontal: 18,
    backgroundColor: theme.colors.ink,
  },
});
