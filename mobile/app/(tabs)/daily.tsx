import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
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

  const summaryText = briefing?.summary ?? 'No briefing yet. Trigger today\'s brief to see it here.';
  const metaText = briefing
    ? `${briefing.event_count} events • ${briefing.todo_count} todos`
    : 'No summary metrics yet';

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <View
        style={[
          styles.content,
          {
            paddingTop: insets.top + 18,
            paddingBottom: insets.bottom + 90,
          },
        ]}
      >
        <Text style={styles.kicker}>Daily</Text>
        <Text style={styles.title}>Your day, scoped</Text>
        <Text style={styles.subtitle}>Review the briefing before you dive in.</Text>

        <View style={styles.summaryCard}>
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
              <Text style={styles.briefingText}>{briefing?.markdown || 'No briefing yet.'}</Text>
            </View>
          ) : null}
          {loading ? <Text style={styles.statusText}>Loading briefing...</Text> : null}
          {!loading && error ? <Text style={styles.statusText}>{error}</Text> : null}
        </View>

        <View style={styles.todoCard}>
          <Text style={styles.todoTitle}>Open todos</Text>
          {todosLoading ? <Text style={styles.todoMeta}>Loading todos...</Text> : null}
          {!todosLoading && todos.length === 0 ? (
            <Text style={styles.todoMeta}>No open todos right now.</Text>
          ) : null}
          {!todosLoading && todos.length > 0 ? (
            <View style={styles.todoList}>
              {todos.map((todo) => (
                <View key={todo.todo_id} style={styles.todoItem}>
                  <Text style={styles.todoBullet}>•</Text>
                  <View style={styles.todoTextWrap}>
                    <Text style={styles.todoText}>{todo.description}</Text>
                    {todo.due_date ? (
                      <Text style={styles.todoMeta}>Due {todo.due_date}</Text>
                    ) : null}
                  </View>
                </View>
              ))}
            </View>
          ) : null}
        </View>
      </View>
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
    borderRadius: theme.radius.lg,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
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
  briefingText: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 19,
  },
  statusText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  pressed: {
    opacity: 0.7,
  },
  todoCard: {
    marginTop: 12,
    padding: 18,
    borderRadius: theme.radius.lg,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
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
  todoList: {
    marginTop: 10,
    gap: 10,
  },
  todoItem: {
    flexDirection: 'row',
    gap: 8,
  },
  todoBullet: {
    fontSize: 16,
    color: theme.colors.accentDeep,
  },
  todoTextWrap: {
    flex: 1,
  },
  todoText: {
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
});
