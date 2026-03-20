import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
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
import { theme } from '@/theme';

type SettingsResponse = {
  pushNotificationsEnabled: boolean;
};

const TOKEN_KEY = 'digitalbrain.expoPushToken';

export default function SettingsScreen() {
  const router = useRouter();
  const { token, signOut } = useAuth();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
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
      style={styles.container}
    >
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

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/about-me')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>About me</Text>
            <Text style={styles.rowSubtitle}>
              View and manage what your Brain has learned about you.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/news-topics')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>News topics</Text>
            <Text style={styles.rowSubtitle}>
              Manage tracked topics for your daily briefing news feed.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/places')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>Places</Text>
            <Text style={styles.rowSubtitle}>
              Browse and edit saved places and map coordinates.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/events')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>Events</Text>
            <Text style={styles.rowSubtitle}>
              Search, view, and edit events with linked contacts and places.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>


        <Button
          label="Sign out"
          onPress={signOut}
          variant="primary"
          style={styles.signOutButton}
        />
      </Animated.ScrollView>

      <CollapsingTopBar
        title="Settings"
        secondaryTitle="Control your Brain"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
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
  navCard: {
    marginTop: 16,
    padding: 0,
    overflow: 'hidden',
  },
  navRow: {
    borderRadius: theme.radius.xl,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 20,
    gap: 12,
  },
  signOutButton: {
    marginTop: 20,
    alignSelf: 'stretch',
    borderRadius: theme.radius.md,
    paddingHorizontal: 18,
    backgroundColor: theme.colors.ink,
  },
});
