import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import React from 'react';
import {
  Animated,
  FlatList,
  LayoutAnimation,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  UIManager,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { Card } from '@/components/Card';
import { useTopNotice } from '@/components/top-notice';
import { theme } from '@/theme';

type DailyBriefing = {
  briefing_id?: string | null;
  date: string;
  timezone: string;
  event_count: number;
  todo_count: number;
  summary: string;
  markdown: string;
};

type TodoItem = {
  todo_id: string;
  description: string;
  status?: string | null;
  due_date?: string | null;
};

function formatToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function formatTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

export default function DailyScreen() {
  const insets = useSafeAreaInsets();
  const [expanded, setExpanded] = React.useState(false);
  const [briefing, setBriefing] = React.useState<DailyBriefing | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [todos, setTodos] = React.useState<TodoItem[]>([]);
  const [todosLoading, setTodosLoading] = React.useState(true);
  const [completingIds, setCompletingIds] = React.useState<Record<string, boolean>>({});
  const { showNotice } = useTopNotice();
  const todoAnimations = React.useRef<Record<string, Animated.Value>>({}).current;
  const AnimatedCard = React.useMemo(() => Animated.createAnimatedComponent(Card), []);

  React.useEffect(() => {
    if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
      UIManager.setLayoutAnimationEnabledExperimental(true);
    }
  }, []);

  React.useEffect(() => {
    let isMounted = true;
    const fetchBriefing = async () => {
      try {
        setLoading(true);
        setError(null);
        const date = formatToday();
        const timezone = formatTimezone();
        const response = await apiFetch(
          `/mobile/briefings/daily?date=${encodeURIComponent(date)}&timezone=${encodeURIComponent(
            timezone
          )}`
        );
        if (isMounted) {
          setBriefing(response as DailyBriefing);
        }
      } catch (err) {
        if (!isMounted) return;
        const message = err instanceof Error ? err.message : 'Unable to load briefing.';
        if (message.toLowerCase().includes('briefing not found')) {
          setBriefing(null);
          setError(null);
          return;
        }
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
  }, []);

  React.useEffect(() => {
    let isMounted = true;
    const fetchTodos = async () => {
      try {
        setTodosLoading(true);
        const response = await apiFetch('/mobile/todos?open_only=true&order=due');
        if (!isMounted) return;
        const items = Array.isArray(response?.todos) ? response.todos : [];
        setTodos(items);
      } catch (err) {
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
  }, []);

  const getTodoAnimation = React.useCallback(
    (todoId: string) => {
      if (!todoAnimations[todoId]) {
        todoAnimations[todoId] = new Animated.Value(0);
      }
      return todoAnimations[todoId];
    },
    [todoAnimations]
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
      } catch (err) {
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

  const summaryText = briefing?.summary ?? 'No briefing yet. Trigger today\'s brief to see it here.';
  const metaText = briefing
    ? `${briefing.event_count} events • ${briefing.todo_count} todos`
    : 'No summary metrics yet';

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <FlatList
        data={todos}
        keyExtractor={(item) => item.todo_id}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: insets.top + 18,
            paddingBottom: insets.bottom + 110,
          },
        ]}
        ListHeaderComponent={
          <View>
            <Text style={styles.kicker}>Daily</Text>
            <Text style={styles.title}>Your day, scoped</Text>
            <Text style={styles.subtitle}>Review the briefing before you dive in.</Text>

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
                    {renderMarkdown(briefing?.markdown || 'No briefing yet.')}
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
              <View style={styles.todoContent}>
                <Text style={styles.todoText}>{item.description}</Text>
                {item.due_date ? <Text style={styles.todoMeta}>Due {item.due_date}</Text> : null}
              </View>
              <View style={styles.todoActionRow}>
                <Pressable
                  onPress={() => handleComplete(item)}
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
            </AnimatedCard>
          );
        }}
        ListEmptyComponent={
          !todosLoading ? (
            <Text style={styles.todoMeta}>No open todos right now.</Text>
          ) : null
        }
      />
    </LinearGradient>
  );
}

function renderMarkdown(markdown: string) {
  return markdown.split('\n').map((line, index) => {
    if (line.startsWith('# ')) {
      return (
        <Text key={`h1-${index}`} style={styles.markdownH1}>
          {line.replace('# ', '')}
        </Text>
      );
    }
    if (line.startsWith('## ')) {
      return (
        <Text key={`h2-${index}`} style={styles.markdownH2}>
          {line.replace('## ', '')}
        </Text>
      );
    }
    if (line.startsWith('### ')) {
      return (
        <Text key={`h3-${index}`} style={styles.markdownH3}>
          {line.replace('### ', '')}
        </Text>
      );
    }
    if (line.startsWith('- ')) {
      return (
        <View key={`bullet-${index}`} style={styles.markdownBulletRow}>
          <Text style={styles.markdownBullet}>•</Text>
          <Text style={styles.markdownBulletText}>{line.replace('- ', '')}</Text>
        </View>
      );
    }
    if (!line.trim()) {
      return <View key={`space-${index}`} style={styles.markdownSpacer} />;
    }
    return (
      <Text key={`p-${index}`} style={styles.markdownParagraph}>
        {line}
      </Text>
    );
  });
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    gap: 12,
  },
  kicker: {
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 3,
    color: theme.colors.teal,
    fontWeight: '600',
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
  markdownH1: {
    fontSize: 18,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownH2: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
    marginTop: 6,
  },
  markdownH3: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
    marginTop: 4,
  },
  markdownParagraph: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 19,
  },
  markdownBulletRow: {
    flexDirection: 'row',
    gap: 8,
  },
  markdownBullet: {
    fontSize: 12,
    color: theme.colors.accentDeep,
    marginTop: 2,
  },
  markdownBulletText: {
    flex: 1,
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 19,
  },
  markdownSpacer: {
    height: 6,
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
  todoCard: {
    marginTop: 12,
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
});
