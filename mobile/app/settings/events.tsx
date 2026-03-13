import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React, { useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
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
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { theme } from '@/theme';

const PAGE_SIZE = 30;

type EventListItem = {
  id: string;
  title?: string | null;
  summary?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

type EventSearchResponse = {
  events: EventListItem[];
  has_more?: boolean;
  next_offset?: number;
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
  const scrollY = useRef(new Animated.Value(0)).current;
  const [events, setEvents] = useState<EventListItem[]>([]);
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const hasLoadedOnceRef = useRef(false);
  const hasMoreRef = useRef(false);
  const nextOffsetRef = useRef(0);
  const isLoadingMoreRef = useRef(false);

  const loadEvents = React.useCallback(
    async ({
      showInitialLoader = false,
      showRefreshSpinner = false,
      append = false,
    }: {
      showInitialLoader?: boolean;
      showRefreshSpinner?: boolean;
      append?: boolean;
    } = {}) => {
      const trimmed = query.trim();
      if (append) {
        if (isLoadingMoreRef.current || !hasMoreRef.current) return;
        isLoadingMoreRef.current = true;
        setIsLoadingMore(true);
      }
      if (showInitialLoader) setIsLoading(true);
      if (showRefreshSpinner) setIsRefreshing(true);
      const requestOffset = append ? nextOffsetRef.current : 0;
      try {
        if (!append) setLoadError(null);
        const searchParams = new URLSearchParams();
        searchParams.set('limit', String(PAGE_SIZE));
        searchParams.set('offset', String(requestOffset));
        if (trimmed) searchParams.set('query', trimmed);
        const result = (await apiFetch(
          `/mobile/events/search?${searchParams.toString()}`,
        )) as EventSearchResponse;
        const incoming = result.events ?? [];
        setEvents((prev) => {
          if (!append) return incoming;
          const seen = new Set(prev.map((event) => event.id));
          const deduped = incoming.filter((event) => !seen.has(event.id));
          return prev.concat(deduped);
        });
        const resolvedNextOffset =
          typeof result.next_offset === 'number'
            ? result.next_offset
            : requestOffset + incoming.length;
        const resolvedHasMore = Boolean(result.has_more);
        nextOffsetRef.current = resolvedNextOffset;
        hasMoreRef.current = resolvedHasMore;
      } catch (error) {
        console.warn('[events] load failed', error);
        setLoadError('Unable to load events. Pull to refresh.');
      } finally {
        if (append) {
          isLoadingMoreRef.current = false;
          setIsLoadingMore(false);
        }
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

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={insets.top + COLLAPSING_TOP_BAR_HEIGHT}
    >
      <FlatList
        data={events}
        keyExtractor={(item) => item.id}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        refreshing={isRefreshing}
        onRefresh={() => void loadEvents({ showRefreshSpinner: true })}
        onEndReachedThreshold={0.35}
        onEndReached={() => void loadEvents({ append: true })}
        automaticallyAdjustKeyboardInsets
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: insets.top + COLLAPSING_TOP_BAR_HEIGHT + COLLAPSING_CONTENT_TOP_PADDING,
            paddingBottom: insets.bottom + 44,
          },
        ]}
        ListHeaderComponent={
          <View style={styles.header}>
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
        ListFooterComponent={
          isLoadingMore ? <ActivityIndicator size="small" color={theme.colors.accent} style={styles.loader} /> : null
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
                {item.summary ? (
                  <Text style={styles.cardMeta} numberOfLines={2}>
                    {item.summary}
                  </Text>
                ) : null}
              </View>
              <Ionicons name="chevron-forward" size={18} color={theme.colors.mutedInk} />
            </Pressable>
          </Card>
        )}
      />
      <CollapsingTopBar
        title="Events"
        secondaryTitle='Dig through your moments'
        scrollY={scrollY}
        onPressBack={() => router.back()}
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
    paddingTop: COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
    gap: 8,
    marginBottom: 8,
  },
  subtitle: {
    marginTop: 6,
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
  cardMeta: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    marginTop: 2,
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
