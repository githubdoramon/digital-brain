import Ionicons from '@expo/vector-icons/Ionicons';
import { useHeaderHeight } from '@react-navigation/elements';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React, { useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';
import { normalizeSearch } from '@/utils/text';

type EventListItem = {
  id: string;
  title?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

type EventSearchResponse = {
  events: EventListItem[];
};

function formatDate(value?: string | null): string {
  if (!value) return 'Date TBD';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export default function SettingsEventsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const headerHeight = useHeaderHeight();
  const [events, setEvents] = useState<EventListItem[]>([]);
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const hasLoadedOnceRef = useRef(false);

  const loadEvents = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false } = {}) => {
      const trimmed = query.trim();
      if (showInitialLoader) setIsLoading(true);
      if (showRefreshSpinner) setIsRefreshing(true);
      try {
        setLoadError(null);
        const searchParams = new URLSearchParams();
        searchParams.set('limit', '50');
        if (trimmed) searchParams.set('query', trimmed);
        const result = (await apiFetch(
          `/mobile/events/search?${searchParams.toString()}`,
        )) as EventSearchResponse;
        setEvents(result.events ?? []);
      } catch (error) {
        console.warn('[events] load failed', error);
        setLoadError('Unable to load events. Pull to refresh.');
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [query],
  );

  useFocusEffect(
    React.useCallback(() => {
      void loadEvents({ showInitialLoader: !hasLoadedOnceRef.current });
      hasLoadedOnceRef.current = true;
      return undefined;
    }, [loadEvents]),
  );

  React.useEffect(() => {
    const timeout = setTimeout(() => {
      void loadEvents({ showInitialLoader: !hasLoadedOnceRef.current });
      hasLoadedOnceRef.current = true;
    }, 220);
    return () => clearTimeout(timeout);
  }, [loadEvents]);

  const filtered = useMemo(() => {
    const normalizedQuery = normalizeSearch(query.trim());
    if (!normalizedQuery) return events;
    return events.filter((event) => normalizeSearch(String(event.title || '')).includes(normalizedQuery));
  }, [events, query]);

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={headerHeight}
    >
      <FlatList
        data={filtered}
        keyExtractor={(item) => item.id}
        refreshing={isRefreshing}
        onRefresh={() => void loadEvents({ showRefreshSpinner: true })}
        automaticallyAdjustKeyboardInsets
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: Platform.OS === 'android' ? 12 : headerHeight + 12,
            paddingBottom: insets.bottom + 44,
          },
        ]}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>Events</Text>
            <Text style={styles.subtitle}>Search your event history and open an event to edit links.</Text>
            <TextInput
              value={query}
              onChangeText={setQuery}
              placeholder="Search events"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.searchInput}
            />
          </View>
        }
        ListEmptyComponent={
          isLoading ? (
            <ActivityIndicator size="small" color={theme.colors.accent} style={styles.loader} />
          ) : loadError ? (
            <Text style={[styles.empty, styles.errorText]}>{loadError}</Text>
          ) : (
            <Text style={styles.empty}>No events found.</Text>
          )
        }
        renderItem={({ item }) => (
          <Card style={styles.card}>
            <Pressable
              style={styles.cardTapArea}
              onPress={() =>
                router.push({
                  pathname: '/events/[eventId]',
                  params: { eventId: item.id },
                })
              }
            >
              <View style={styles.cardBody}>
                <Text style={styles.cardTitle}>{String(item.title || '').trim() || 'Untitled event'}</Text>
                <Text style={styles.cardSubtitle}>{formatDate(item.start_date)}</Text>
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Edit event"
                onPress={(event) => {
                  event?.stopPropagation?.();
                  router.push({
                    pathname: '/events/[eventId]',
                    params: { eventId: item.id, editable: '1' },
                  });
                }}
                style={({ pressed }) => [styles.editButton, pressed && styles.editButtonPressed]}
              >
                <Ionicons name="create-outline" size={16} color={theme.colors.ink} />
                <Text style={styles.editLabel}>Edit</Text>
              </Pressable>
            </Pressable>
          </Card>
        )}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  content: {
    paddingHorizontal: 20,
    gap: 14,
  },
  header: {
    gap: 10,
    marginBottom: 8,
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  searchInput: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: theme.colors.ink,
  },
  card: {
    padding: 0,
  },
  cardTapArea: {
    paddingHorizontal: 14,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  cardBody: {
    flex: 1,
    gap: 4,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  cardSubtitle: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  editButton: {
    minHeight: 34,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  editButtonPressed: {
    opacity: 0.8,
  },
  editLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  empty: {
    fontSize: 14,
    textAlign: 'center',
    marginTop: 18,
    color: theme.colors.mutedInk,
  },
  errorText: {
    color: theme.colors.accent,
  },
  loader: {
    marginTop: 24,
  },
});
