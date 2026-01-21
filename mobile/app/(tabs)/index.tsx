import { LinearGradient } from 'expo-linear-gradient';
import React, { useMemo, useState } from 'react';
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/src/api/client';
import { useAuth } from '@/src/auth/AuthContext';
import { theme } from '@/src/theme';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
};

const starterMessages: Message[] = [
  {
    id: 'welcome',
    role: 'assistant',
    content: 'Good to see you. What are we exploring today?',
  },
];

export default function ChatScreen() {
  const { token, signOut } = useAuth();
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>(starterMessages);
  const [input, setInput] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);

  const canSend = input.trim().length > 0 && !isSending;

  const header = useMemo(
    () => (
      <View style={styles.header}>
        <Text style={styles.kicker}>Focus Mode</Text>
        <Text style={styles.title}>Ask your memory vault</Text>
        <Text style={styles.subtitle}>
          Chat with your LLM in a calm, chat-first space.
        </Text>
      </View>
    ),
    [],
  );

  const sendMessage = async () => {
    if (!canSend) return;
    const trimmed = input.trim();
    setInput('');
    const pendingId = `${Date.now()}-pending`;

    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-user`, role: 'user', content: trimmed },
      { id: pendingId, role: 'assistant', content: 'Thinking...', pending: true },
    ]);

    setIsSending(true);
    try {
      const payload = { question: trimmed, thread_id: threadId };
      const response = await apiFetch('/mobile/ask', {
        method: 'POST',
        body: JSON.stringify(payload),
        token,
      });

      setThreadId(response.thread_id ?? threadId);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? { ...message, content: response.answer ?? 'Ready when you are.', pending: false }
            : message,
        ),
      );
    } catch (error) {
      const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
      if (authExpired) {
        await signOut();
        setMessages((prev) =>
          prev.map((message) =>
            message.id === pendingId
              ? {
                  ...message,
                  content: 'Session expired. Please sign in again.',
                  pending: false,
                }
              : message,
          ),
        );
        return;
      }
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? {
                ...message,
                content: 'I hit a snag reaching the brain. Try again in a moment.',
                pending: false,
              }
            : message,
        ),
      );
    } finally {
      setIsSending(false);
    }
  };

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <KeyboardAvoidingView
        style={styles.container}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={90}
      >
        <FlatList
          data={messages}
          keyExtractor={(item) => item.id}
          ListHeaderComponent={header}
          contentContainerStyle={[
            styles.list,
            { paddingTop: insets.top + 16, paddingBottom: 140 + insets.bottom },
          ]}
          renderItem={({ item }) => (
            <View
              style={[
                styles.messageBubble,
                item.role === 'user' ? styles.userBubble : styles.assistantBubble,
              ]}
            >
              <Text
                style={[
                  styles.messageText,
                  item.role === 'user' ? styles.userText : styles.assistantText,
                ]}
              >
                {item.content}
              </Text>
            </View>
          )}
        />

        <View style={[styles.composer, { paddingBottom: 14 + insets.bottom }]}>
          <TextInput
            value={input}
            onChangeText={setInput}
            placeholder="Send a message..."
            placeholderTextColor={theme.colors.mutedInk}
            style={styles.input}
            multiline
          />
          <Pressable
            onPress={sendMessage}
            disabled={!canSend}
            style={({ pressed }) => [
              styles.sendButton,
              !canSend && styles.sendDisabled,
              pressed && canSend && styles.sendPressed,
            ]}
          >
            <Text style={styles.sendText}>{isSending ? '...' : 'Send'}</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  list: {
    paddingHorizontal: 20,
    paddingBottom: 140,
  },
  header: {
    marginTop: 24,
    marginBottom: 20,
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
    marginTop: 6,
  },
  subtitle: {
    marginTop: 6,
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  messageBubble: {
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: theme.radius.lg,
    marginBottom: 12,
    maxWidth: '82%',
  },
  userBubble: {
    alignSelf: 'flex-end',
    backgroundColor: theme.colors.ink,
  },
  assistantBubble: {
    alignSelf: 'flex-start',
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  messageText: {
    fontSize: 15,
    lineHeight: 21,
  },
  userText: {
    color: '#fff',
  },
  assistantText: {
    color: theme.colors.ink,
  },
  composer: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 16,
    paddingVertical: 14,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
    flexDirection: 'row',
    gap: 10,
    alignItems: 'flex-end',
  },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 120,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    color: theme.colors.ink,
  },
  sendButton: {
    backgroundColor: theme.colors.accent,
    borderRadius: theme.radius.md,
    paddingVertical: 12,
    paddingHorizontal: 18,
  },
  sendDisabled: {
    backgroundColor: theme.colors.line,
  },
  sendPressed: {
    transform: [{ scale: 0.97 }],
  },
  sendText: {
    color: '#fff',
    fontWeight: '600',
  },
});
