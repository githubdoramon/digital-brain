import { LinearGradient } from 'expo-linear-gradient';
import React from 'react';
import { useFocusEffect } from '@react-navigation/native';
import {
  Animated,
  FlatList,
  LayoutAnimation,
  Platform,
  StyleSheet,
  Text,
  UIManager,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import Ionicons from '@expo/vector-icons/Ionicons';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { renderAssistantMarkdown } from '@/components/MarkdownRenderer';
import { useTopNotice } from '@/components/top-notice';
import { theme } from '@/theme';

type DailyBriefing = {
  status?: 'ready' | 'pending' | 'failed';
  job_id?: string | null;
  message?: string | null;
  briefing_id?: string | null;
  date: string;
  timezone: string;
  event_count: number;
  todo_count: number;
  summary: string;
  markdown: string;
  news_items?: BriefingNewsItem[];
};

type BriefingNewsItem = {
  briefing_item_id: string;
  briefing_id?: string | null;
  cluster_id?: string | null;
  title: string;
  url?: string | null;
  source?: string | null;
  source_domain?: string | null;
  section: 'topic' | 'general';
  topic_label?: string | null;
  rank: number;
  score?: number | null;
  brief_summary?: string | null;
};

type TodoItem = {
  todo_id: string;
  description: string;
  status?: string | null;
  due_date?: string | null;
  events?: {
    id: string;
    title?: string | null;
    start_date?: string | null;
  }[];
};

function formatToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function formatTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

function normalizeArticleUrl(url: string): string {
  const value = (url || '').trim();
  if (!value) return '';
  try {
    const parsed = new URL(value.startsWith('http') ? value : `https://${value}`);
    const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
    const path = parsed.pathname.replace(/\/+$/, '') || '/';
    return `${host}${path}`;
  } catch {
    return value.toLowerCase();
  }
}

function buildFallbackBriefingItemId(url: string): string {
  const slug = normalizeArticleUrl(url).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug ? `external-${slug.slice(0, 56)}` : 'external-link';
}

export default function DailyScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { email, name, photo, token, isLoading: authLoading, refreshToken } = useAuth();
  const [expanded, setExpanded] = React.useState(false);
  const [briefing, setBriefing] = React.useState<DailyBriefing | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [pendingJobId, setPendingJobId] = React.useState<string | null>(null);
  const [todos, setTodos] = React.useState<TodoItem[]>([]);
  const [todosLoading, setTodosLoading] = React.useState(true);
  const [completingIds, setCompletingIds] = React.useState<Record<string, boolean>>({});
  const { showNotice } = useTopNotice();
  const todoAnimations = React.useRef<Record<string, Animated.Value>>({}).current;
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const AnimatedCard = React.useMemo(() => Animated.createAnimatedComponent(Card), []);

  React.useEffect(() => {
    if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
      UIManager.setLayoutAnimationEnabledExperimental(true);
    }
  }, []);

  const loadBriefing = React.useCallback((options: { showLoading?: boolean } = {}) => {
    let isMounted = true;
    const fetchBriefing = async () => {
      try {
        const showLoading = options.showLoading ?? true;
        if (showLoading) {
          setLoading(true);
        }
        setError(null);
        const date = formatToday();
        const timezone = formatTimezone();
        const response = await apiFetch(
          `/mobile/briefings/daily?date=${encodeURIComponent(date)}&timezone=${encodeURIComponent(
            timezone
          )}`,
          {
            token,
            onAuthExpired: refreshToken,
          }
        );
        if (isMounted) {
          const payload = (response ?? null) as DailyBriefing | null;
          setBriefing(payload);
          if (payload?.status === 'pending') {
            setPendingJobId(payload.job_id ?? 'pending');
            setError(payload.message ?? null);
          } else if (payload?.status === 'failed') {
            setPendingJobId(null);
            setError(payload.message ?? 'Daily briefing generation failed.');
          } else {
            setPendingJobId(null);
            setError(null);
          }
        }
      } catch (err) {
        if (!isMounted) return;
        const message = err instanceof Error ? err.message : 'Unable to load briefing.';
        if (message.toLowerCase().includes('expected json response but got text/html')) {
          setError(null);
          return;
        }
        setPendingJobId(null);
        setError(message);
        setBriefing(null);
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };
    fetchBriefing();
    return () => {
      isMounted = false;
    };
  }, [refreshToken, token]);

  const loadTodos = React.useCallback(() => {
    let isMounted = true;
    const fetchTodos = async () => {
      try {
        setTodosLoading(true);
        const response = await apiFetch('/mobile/todos?open_only=true&order=due', {
          token,
          onAuthExpired: refreshToken,
        });
        if (!isMounted) return;
        const items = Array.isArray(response?.todos) ? response.todos : [];
        setTodos(items);
      } catch {
        if (!isMounted) return;
        setTodos([]);
      } finally {
        if (isMounted) {
          setTodosLoading(false);
        }
      }
    };
    fetchTodos();
    return () => {
      isMounted = false;
    };
  }, [refreshToken, token]);

  useFocusEffect(
    React.useCallback(() => {
      if (authLoading || !token) {
        return;
      }
      const briefingCleanup = loadBriefing();
      const todosCleanup = loadTodos();
      return () => {
        briefingCleanup();
        todosCleanup();
      };
    }, [authLoading, token, loadBriefing, loadTodos])
  );

  React.useEffect(() => {
    if (!pendingJobId || authLoading || !token) {
      return;
    }

    const cleanups = new Set<() => void>();
    const interval = setInterval(() => {
      const cleanup = loadBriefing({ showLoading: false });
      cleanups.add(cleanup);
    }, 10000);

    return () => {
      clearInterval(interval);
      cleanups.forEach((cleanup) => cleanup());
      cleanups.clear();
    };
  }, [authLoading, pendingJobId, token, loadBriefing]);

  const getTodoAnimation = React.useCallback(
    (todoId: string) => {
      if (!todoAnimations[todoId]) {
        todoAnimations[todoId] = new Animated.Value(0);
      }
      return todoAnimations[todoId];
    },
    [todoAnimations]
  );

  const newsItemsByUrl = React.useMemo(() => {
    const map = new Map<string, BriefingNewsItem>();
    for (const item of briefing?.news_items ?? []) {
      const key = normalizeArticleUrl(item.url ?? '');
      if (!key) continue;
      map.set(key, item);
    }
    return map;
  }, [briefing?.news_items]);

  const handleBriefingLinkPress = React.useCallback(
    async (url: string, label?: string) => {
      const normalized = normalizeArticleUrl(url);
      const matched = normalized ? newsItemsByUrl.get(normalized) : undefined;
      const briefingItemId = matched?.briefing_item_id || buildFallbackBriefingItemId(url);
      router.push({
        pathname: '/news/article/[briefingItemId]',
        params: {
          briefingItemId,
          url,
          briefingId: matched?.briefing_id || briefing?.briefing_id || '',
          clusterId: matched?.cluster_id || '',
          source: matched?.source || '',
          sourceDomain: matched?.source_domain || '',
          topicLabel: matched?.topic_label || '',
          title: matched?.title || label || '',
        },
      });
    },
    [briefing?.briefing_id, newsItemsByUrl, router],
  );

  const handleComplete = React.useCallback(
    async (todo: TodoItem) => {
      if (completingIds[todo.todo_id]) return;
      setCompletingIds((prev) => ({ ...prev, [todo.todo_id]: true }));
      try {
        await apiFetch(`/mobile/todos/${todo.todo_id}/status`, {
          method: 'PATCH',
          body: JSON.stringify({ status: 'completed' }),
        });
        const animation = getTodoAnimation(todo.todo_id);
        Animated.timing(animation, {
          toValue: 1,
          duration: 220,
          useNativeDriver: false,
        }).start(() => {
          LayoutAnimation.configureNext({
            duration: 220,
            update: {
              type: LayoutAnimation.Types.easeInEaseOut,
              property: LayoutAnimation.Properties.opacity,
            },
            delete: {
              type: LayoutAnimation.Types.easeInEaseOut,
              property: LayoutAnimation.Properties.opacity,
            },
          });
          setTodos((prev) => prev.filter((item) => item.todo_id !== todo.todo_id));
        });
        showNotice('Todo marked as completed.', 'success');
      } catch {
        showNotice('Unable to update todo.', 'error');
      } finally {
        setCompletingIds((prev) => {
          const next = { ...prev };
          delete next[todo.todo_id];
          return next;
        });
      }
    },
    [completingIds, getTodoAnimation, showNotice]
  );

  const isBriefingPending = briefing?.status === 'pending' || !!pendingJobId;
  const summaryText = isBriefingPending
    ? briefing?.message || 'Generating your briefing now...'
    : briefing?.summary ?? 'No briefing yet. Trigger today\'s brief to see it here.';
  const metaText = isBriefingPending
    ? 'Generating now'
    : briefing
      ? `${briefing.event_count} events • ${briefing.todo_count} todos`
      : 'No summary metrics yet';
  const formatEventTitle = (event: TodoItem['events'][number]) =>
    event.title?.trim() || 'Linked event';
  const profileName = name || email || 'You';

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <FlatList
        data={todos}
        keyExtractor={(item) => item.todo_id}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: insets.top + COLLAPSING_TOP_BAR_HEIGHT + COLLAPSING_CONTENT_TOP_PADDING,
            paddingBottom: insets.bottom + 220,
          },
        ]}
        ListHeaderComponent={
          <View style={styles.headerSpacer} >
            <Card style={styles.summaryCard}>
              <Pressable
                onPress={() => setExpanded((prev) => !prev)}
                style={({ pressed }) => [styles.summaryHeader, pressed && styles.pressed]}
              >
                <View>
                  <Text style={styles.summaryTitle}>Daily briefing</Text>
                  <Text style={styles.summaryMeta}>{metaText}</Text>
                </View>
                <Ionicons
                  name={expanded ? 'chevron-up' : 'chevron-down'}
                  size={20}
                  color={theme.colors.mutedInk}
                />
              </Pressable>
              <Text style={styles.summaryBody}>{summaryText}</Text>
              {expanded ? (
                <View style={styles.briefingBlock}>
                  <Text style={styles.briefingLabel}>Full briefing</Text>
                  <View style={styles.markdownBlock}>
                    {renderAssistantMarkdown(briefing?.markdown || 'No briefing yet.', 'briefing', {
                      onLinkPress: handleBriefingLinkPress,
                    })}
                  </View>
                </View>
              ) : null}
              {loading ? <Text style={styles.statusText}>Loading briefing...</Text> : null}
              {!loading && error ? <Text style={styles.statusText}>{error}</Text> : null}
            </Card>

            <View style={styles.todoHeader}>
              <Text style={styles.todoTitle}>Open todos</Text>
              {todosLoading ? <Text style={styles.todoMeta}>Loading todos...</Text> : null}
            </View>
          </View>
        }
        renderItem={({ item }) => {
          const animation = getTodoAnimation(item.todo_id);
          const translateX = animation.interpolate({
            inputRange: [0, 1],
            outputRange: [0, 320],
          });
          const opacity = animation.interpolate({
            inputRange: [0, 0.6, 1],
            outputRange: [1, 1, 0],
          });
          const height = animation.interpolate({
            inputRange: [0, 1],
            outputRange: [0, 1],
          });
          const primaryEvent = item.events?.[0];
          const eventCount = item.events?.length ?? 0;
          return (
            <AnimatedCard
              variant="elevated"
              style={[
                styles.todoCard,
                {
                  transform: [{ translateX }],
                  opacity,
                  maxHeight: height.interpolate({
                    inputRange: [0, 1],
                    outputRange: [220, 0],
                  }),
                  marginBottom: height.interpolate({
                    inputRange: [0, 1],
                    outputRange: [0, -12],
                  }),
                },
              ]}
            >
              <Pressable
                onPress={() => router.push(`/todos/${encodeURIComponent(item.todo_id)}`)}
                style={styles.todoCardTapArea}
              >
                <View style={styles.todoContent}>
                  <Text style={styles.todoText}>{item.description}</Text>
                  {primaryEvent ? (
                    <Pressable
                      onPress={(event) => {
                        event?.stopPropagation?.();
                        router.push(`/events/${encodeURIComponent(primaryEvent.id)}`);
                      }}
                      style={({ pressed }) => [
                        styles.todoEventRow,
                        pressed && styles.todoEventRowPressed,
                      ]}
                    >
                      <Ionicons name="calendar" size={14} color={theme.colors.accentDeep} />
                      <Text style={styles.todoEventText}>{formatEventTitle(primaryEvent)}</Text>
                      {eventCount > 1 ? (
                        <Text style={styles.todoEventMeta}>+{eventCount - 1}</Text>
                      ) : null}
                    </Pressable>
                  ) : null}
                  {item.due_date ? <Text style={styles.todoMeta}>Due {item.due_date}</Text> : null}
                </View>
                <View style={styles.todoActionRow}>
                  <Pressable
                    onPress={(event) => {
                      event?.stopPropagation?.();
                      handleComplete(item);
                    }}
                    disabled={!!completingIds[item.todo_id]}
                    style={({ pressed }) => [
                      styles.todoAction,
                      pressed && styles.pressed,
                      completingIds[item.todo_id] && styles.todoActionDisabled,
                    ]}
                  >
                    <Ionicons name="checkmark" size={18} color={theme.colors.accentDeep} />
                    <Text style={styles.todoActionText}>Done</Text>
                  </Pressable>
                </View>
              </Pressable>
            </AnimatedCard>
          );
        }}
        ListEmptyComponent={
          !todosLoading ? (
            <Text style={styles.todoMeta}>No open todos right now.</Text>
          ) : null
        }
      />
      <CollapsingTopBar
        title="Daily"
        secondaryTitle="Your day, scoped"
        scrollY={scrollY}
        profileName={profileName}
        profilePhoto={photo}
        token={token}
        onPressProfile={() => router.push('/settings')}
      />
      <Pressable
        onPress={() => router.push('/todos')}
        accessibilityRole="button"
        accessibilityLabel="Add a todo"
        style={({ pressed }) => [
          styles.fab,
          { bottom: insets.bottom + 96 },
          pressed && styles.fabPressed,
        ]}
      >
        <Ionicons name="add" size={26} color="#fff" />
      </Pressable>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    gap: 12,
  },
  headerSpacer: {
    paddingTop: COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  },
  subtitle: {
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  summaryCard: {
    marginTop: 10,
    padding: 18,
    gap: 10,
  },
  summaryHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  summaryTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  summaryMeta: {
    fontSize: 12,
    color: theme.colors.mutedInk,
    marginTop: 2,
  },
  summaryBody: {
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
  briefingBlock: {
    paddingTop: 6,
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
    gap: 6,
  },
  briefingLabel: {
    fontSize: 12,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 2,
    color: theme.colors.mutedInk,
  },
  markdownBlock: {
    gap: 6,
  },
  statusText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  pressed: {
    opacity: 0.7,
  },
  todoHeader: {
    marginTop: 12,
  },
  todoTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  todoMeta: {
    marginTop: 6,
    fontSize: 14,
    color: theme.colors.mutedInk,
    lineHeight: 20,
  },
  todoEventRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 6,
    borderRadius: 12,
    paddingVertical: 4,
    paddingHorizontal: 6,
    marginHorizontal: -6,
    alignSelf: 'flex-start',
  },
  todoEventRowPressed: {
    backgroundColor: 'rgba(47, 111, 116, 0.12)',
  },
  todoEventText: {
    fontSize: 13,
    color: theme.colors.ink,
    fontWeight: '600',
    flex: 1,
  },
  todoEventMeta: {
    fontSize: 11,
    color: theme.colors.mutedInk,
  },
  todoCard: {
    marginTop: 12,
  },
  todoCardTapArea: {
    borderRadius: theme.radius.lg,
    padding: 16,
    gap: 12,
    flexDirection: 'column',
  },
  todoContent: {
    gap: 6,
  },
  todoActionRow: {
    justifyContent: 'flex-end',
    flexDirection: 'row',
  },
  todoText: {
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
  todoAction: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: 'rgba(255, 255, 255, 0.7)',
  },
  todoActionText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  todoActionDisabled: {
    opacity: 0.6,
  },
  fab: {
    position: 'absolute',
    right: 22,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: '#0f1113',
    shadowOpacity: 0.38,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: 16 },
    elevation: 14,
  },
  fabPressed: {
    transform: [{ scale: 0.97 }],
    shadowOpacity: 0.18,
  },
});
