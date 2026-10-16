import { LinearGradient } from 'expo-linear-gradient';
import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useRouter } from 'expo-router';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { ActivityIndicator, FlatList, StyleSheet, Text, TextInput, View } from 'react-native';

import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { listNonCommandThreads, type ThreadSummary } from '@/chat/threads';
import { loadChatSession, loadPendingRun } from '@/chat/session';
import { getUnreadThreadIds } from '@/chat/threadNotifications';
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
  const [unreadThreadIds, setUnreadThreadIds] = useState<Set<string>>(new Set());
  const [pendingThreadId, setPendingThreadId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const requestIdRef = useRef(0);

  useEffect(() => {
    const timeout = setTimeout(() => setDebouncedQuery(searchText.trim()), 350);
    return () => clearTimeout(timeout);
  }, [searchText]);

  const loadThreads = useCallback(async (query: string) => {
    const requestId = ++requestIdRef.current;
    if (!token) {
      setThreads([]);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const [nextThreads, pendingRun, mainSession] = await Promise.all([
        listNonCommandThreads(token, query),
        loadPendingRun(),
        loadChatSession(),
      ]);
      const unreadIds = await getUnreadThreadIds(nextThreads);
      if (mainSession?.threadId) unreadIds.delete(mainSession.threadId);
      if (requestId === requestIdRef.current) {
        setThreads(nextThreads);
        setUnreadThreadIds(unreadIds);
        setPendingThreadId(
          pendingRun?.threadId && pendingRun.threadId !== mainSession?.threadId
            ? pendingRun.threadId
            : null,
        );
      }
    } catch {
      if (requestId === requestIdRef.current) {
        setError('Could not load thread history right now.');
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, [token]);

  useFocusEffect(
    useCallback(() => {
      if (isAuthLoading) return;
      void loadThreads(debouncedQuery);
      return () => {
        requestIdRef.current += 1;
      };
    }, [debouncedQuery, isAuthLoading, loadThreads]),
  );

  const toggleSearch = useCallback(() => {
    if (isSearchOpen) {
      setIsSearchOpen(false);
      setSearchText('');
      setDebouncedQuery('');
      return;
    }
    setIsSearchOpen(true);
  }, [isSearchOpen]);

  return (
    <>
      <Stack.Screen
        options={{
          headerTitle: isSearchOpen
            ? () => (
                <TextInput
                  autoFocus
                  value={searchText}
                  onChangeText={setSearchText}
                  placeholder="Search conversations"
                  placeholderTextColor={theme.colors.mutedInk}
                  autoCapitalize="none"
                  autoCorrect={false}
                  returnKeyType="search"
                  style={styles.searchInput}
                  accessibilityLabel="Search threads"
                />
              )
            : 'Thread history',
          headerRight: () => (
            <Pressable
              onPress={toggleSearch}
              accessibilityRole="button"
              accessibilityLabel={isSearchOpen ? 'Close thread search' : 'Search threads'}
              style={({ pressed }) => [styles.searchButton, pressed && styles.itemPressed]}
            >
              <Ionicons
                name={isSearchOpen ? 'close' : 'search'}
                size={21}
                color={theme.colors.ink}
              />
            </Pressable>
          ),
        }}
      />
      <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      {isLoading && threads.length === 0 ? (
        <View style={styles.centerState}>
          <ActivityIndicator color={theme.colors.accent} />
        </View>
      ) : error ? (
        <View style={styles.centerState}>
          <Text style={styles.stateText}>{error}</Text>
        </View>
      ) : threads.length === 0 ? (
        <View style={styles.centerState}>
          <Text style={styles.stateTitle}>
            {debouncedQuery ? 'No conversations found' : 'No chat threads yet'}
          </Text>
          <Text style={styles.stateText}>
            {debouncedQuery
              ? 'Try a different search.'
              : 'Your regular conversations will appear here.'}
          </Text>
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
                {unreadThreadIds.has(item.id) || pendingThreadId === item.id ? (
                  <View style={styles.notificationDot} accessibilityLabel="Thread has an update" />
                ) : null}
                <Text style={styles.cardTitle} numberOfLines={1}>
                  {(item.title || 'Untitled thread').trim() || 'Untitled thread'}
                </Text>
                {(debouncedQuery
                  ? item.matched_message_preview || item.last_message_preview
                  : item.last_message_preview) ? (
                  <Text style={styles.cardPreview} numberOfLines={2}>
                    {debouncedQuery
                      ? item.matched_message_preview || item.last_message_preview
                      : item.last_message_preview}
                  </Text>
                ) : null}
                <Text style={styles.cardMeta}>{`Updated ${formatThreadDate(item.updated_at)}`}</Text>
              </Card>
            </Pressable>
          )}
        />
      )}
      {isLoading && threads.length > 0 ? (
        <View pointerEvents="none" style={styles.loadingOverlay}>
          <ActivityIndicator color={theme.colors.accent} />
        </View>
      ) : null}
      </LinearGradient>
    </>
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
    paddingBottom: 32,
  },
  loadingOverlay: {
    position: 'absolute',
    top: 12,
    right: 20,
  },
  searchButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  searchInput: {
    width: 220,
    paddingVertical: 6,
    fontSize: 17,
    color: theme.colors.ink,
  },
  card: {
    padding: 16,
    gap: 10,
    position: 'relative',
  },
  notificationDot: {
    position: 'absolute',
    top: 14,
    right: 14,
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: '#F28C28',
  },
  itemPressed: {
    opacity: 0.82,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: theme.colors.ink,
    paddingRight: 20,
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
