import { LinearGradient } from 'expo-linear-gradient';
import React, { useEffect, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Switch, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/src/api/client';
import { useAuth } from '@/src/auth/AuthContext';
import { registerForPushNotifications } from '@/src/notifications/register';
import { theme } from '@/src/theme';

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

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const response = (await apiFetch('/settings', { token })) as SettingsResponse;
        if (mounted) {
          setPushEnabled(Boolean(response?.pushNotificationsEnabled));
          if (response?.pushNotificationsEnabled) {
            await ensureDeviceRegistered();
          }
        }
      } catch (error) {
        // noop: keep defaults
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [token]);

  const ensureDeviceRegistered = async () => {
    const existing = await SecureStore.getItemAsync(TOKEN_KEY);
    if (existing) {
      return;
    }
    const registration = await registerForPushNotifications();
    if (!registration) {
      Alert.alert('Notifications disabled', 'Enable notifications in system settings to continue.');
      return;
    }
    await apiFetch('/devices/register', {
      method: 'POST',
      body: JSON.stringify(registration),
      token,
    });
    await SecureStore.setItemAsync(TOKEN_KEY, registration.expoPushToken);
  };

  const unregisterDevice = async () => {
    const existing = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!existing) return;
    await apiFetch(`/devices/unregister?expoPushToken=${encodeURIComponent(existing)}`, {
      method: 'DELETE',
      token,
    });
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  };

  const togglePush = async (value: boolean) => {
    setPushEnabled(value);
    setIsSaving(true);
    try {
      await apiFetch('/settings/push-notifications', {
        method: 'PUT',
        body: JSON.stringify({ enabled: value }),
        token,
      });
      if (value) {
        await ensureDeviceRegistered();
      } else {
        await unregisterDevice();
      }
    } catch (error) {
      setPushEnabled((prev) => !prev);
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
        <Text style={styles.title}>Control your signal</Text>
        <Text style={styles.subtitle}>
          Personalize how the Digital Brain reaches you.
        </Text>
      </View>

      <View style={styles.card}>
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
      </View>

      <Pressable
        onPress={signOut}
        style={({ pressed }) => [styles.signOutButton, pressed && styles.signOutPressed]}
      >
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
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
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.xl,
    padding: 20,
    borderWidth: 1,
    borderColor: theme.colors.line,
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
    alignSelf: 'flex-start',
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    paddingVertical: 12,
    paddingHorizontal: 18,
    backgroundColor: theme.colors.card,
  },
  signOutPressed: {
    transform: [{ scale: 0.98 }],
  },
  signOutText: {
    color: theme.colors.mutedInk,
    fontWeight: '600',
  },
});
