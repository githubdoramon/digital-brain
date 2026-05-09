import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { ActivityIndicator, FlatList, StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { listNonCommandThreads, type ThreadSummary } from '@/chat/threads';
import { theme } from '@/theme';

function formatThreadDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return '';
  }
  return parsed.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export default function ThreadHistoryScreen() {
  const router = useRouter();
  const { token, isLoading: isAuthLoading } = useAuth();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadThreads = useCallback(async () => {
    if (!token) {
      setThreads([]);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const nextThreads = await listNonCommandThreads(token);
      setThreads(nextThreads);
    } catch {
      setError('Could not load thread history right now.');
    } finally {
      setIsLoading(false);
    }
  }, [token]);

  useFocusEffect(
    useCallback(() => {
      if (isAuthLoading) return;
      void loadThreads();
    }, [isAuthLoading, loadThreads]),
  );

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      {isLoading ? (
        <View style={styles.centerState}>
          <ActivityIndicator color={theme.colors.accent} />
        </View>
      ) : error ? (
        <View style={styles.centerState}>
          <Text style={styles.stateText}>{error}</Text>
        </View>
      ) : threads.length === 0 ? (
        <View style={styles.centerState}>
          <Text style={styles.stateTitle}>No chat threads yet</Text>
          <Text style={styles.stateText}>Only regular conversations appear here. Command threads stay hidden.</Text>
        </View>
      ) : (
        <FlatList
          data={threads}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listContent}
          renderItem={({ item }) => (
            <Pressable
              onPress={() => router.push(`/chat/${encodeURIComponent(item.id)}`)}
              style={({ pressed }) => [pressed && styles.itemPressed]}
            >
              <Card variant="elevated" style={styles.card}>
                <Text style={styles.cardTitle} numberOfLines={1}>
                  {(item.title || 'Untitled thread').trim() || 'Untitled thread'}
                </Text>
                {item.last_message_preview ? (
                  <Text style={styles.cardPreview} numberOfLines={2}>
                    {item.last_message_preview}
                  </Text>
                ) : null}
                <Text style={styles.cardMeta}>{`Updated ${formatThreadDate(item.updated_at)}`}</Text>
              </Card>
            </Pressable>
          )}
        />
      )}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  centerState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 28,
    gap: 8,
  },
  stateTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  stateText: {
    fontSize: 14,
    lineHeight: 20,
    color: theme.colors.mutedInk,
    textAlign: 'center',
  },
  listContent: {
    padding: 20,
    gap: 12,
  },
  card: {
    padding: 16,
    gap: 10,
  },
  itemPressed: {
    opacity: 0.82,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  cardPreview: {
    fontSize: 14,
    lineHeight: 20,
    color: theme.colors.mutedInk,
  },
  cardMeta: {
    fontSize: 12,
    color: theme.colors.teal,
    textTransform: 'uppercase',
    letterSpacing: 1.2,
  },
});
