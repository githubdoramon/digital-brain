import { LinearGradient } from 'expo-linear-gradient';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  KeyboardAvoidingViewProps,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { theme } from '@/theme';
import { Button } from '@/components/Button';
import { EventClarificationCard } from '@/components/EventClarificationCard';
import { EventProposalCard } from '@/components/EventProposalCard';
import { SlashCommandPalette } from '@/components/SlashCommandPalette';
import { loadChatSession, saveChatSession, StoredChatSession } from '@/chat/session';
import { restoreChatHistory } from '@/chat/threads';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
  metadata?: {
    command_result?: CommandResult;
  };
};

type EventClarificationData = {
  type: 'clarification_needed';
  questions: string[];
  partial_extraction: Record<string, unknown>;
  original_message: string;
  clarification_id?: string;
};

type EventConfirmationData = {
  type: 'event_confirmation';
  preview_id: string;
  extracted: {
    title: string;
    summary: string;
    when: string | null;
    where: string | null;
    who: string[];
    documents: string[];
    tags: string[];
    types: string[];
  };
  resolution: {
    contacts: {
      contact_id: string;
      display_name: string;
      query: string;
      confidence: string;
    }[];
    places: {
      place_id: string;
      name: string;
    }[];
    documents: {
      document_id: string;
      title: string;
    }[];
    new_entities: {
      contacts: {
        display_name: string;
        query: string;
      }[];
      places: {
        name: string;
        query: string;
      }[];
      documents: {
        reference: string;
      }[];
    };
  };
  relationship_suggestions?: {
    from_contact_id: string;
    from_display_name: string;
    to_contact_id: string;
    to_display_name: string;
    relationship_type: string;
    reciprocal_type: string;
    confidence: string;
    reasoning: string;
  }[];
  message: string;
};

type CommandResult = EventClarificationData | EventConfirmationData;

const useKeyboardBehavior = () => {
  const defaultBehavior: KeyboardAvoidingViewProps['behavior'] =
    Platform.OS === 'ios' ? 'padding' : 'height';
  const [behavior, setBehavior] = useState<KeyboardAvoidingViewProps['behavior']>(defaultBehavior);

  useEffect(() => {
    const showListener = Keyboard.addListener('keyboardDidShow', () => {
      setBehavior(defaultBehavior);
    });
    const hideListener = Keyboard.addListener('keyboardDidHide', () => {
      setBehavior(undefined);
    });

    return () => {
      showListener.remove();
      hideListener.remove();
    };
  }, [defaultBehavior]);

  return behavior;
};

export default function ChatScreen() {
  const { token, signOut, email } = useAuth();
  const insets = useSafeAreaInsets();
  const listRef = useRef<FlatList<Message>>(null);
  const [input, setInput] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const keyboardBehavior = useKeyboardBehavior();
  const [isConfirmingEvent, setIsConfirmingEvent] = useState(false);
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  const [listHeight, setListHeight] = useState(0);
  const [contentHeight, setContentHeight] = useState(0);
  const [lastMessageHeight, setLastMessageHeight] = useState(0);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [forceScrollNext, setForceScrollNext] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);

  const allowed = email === 'REDACTED-EMAIL';
  const canSend = input.trim().length > 0 && !isSending && allowed;

  const starterMessages = useMemo<Message[]>(
    () => [
      {
        id: 'welcome',
        role: 'assistant',
        content: allowed
          ? 'Good to see you. What are we exploring today?'
          : 'Access restricted. Please contact the administrator.',
      },
    ],
    [allowed],
  );
  const [messages, setMessages] = useState<Message[]>(starterMessages);

  useEffect(() => {
    if (messages.length === 1 && messages[0]?.id === 'welcome') {
      setMessages(starterMessages);
    }
  }, [starterMessages, messages]);

  useEffect(() => {
    const restoreSession = async () => {
      if (!token || !allowed) {
        setIsBootstrapping(false);
        return;
      }

      try {
        const stored = await loadChatSession();
        const restored = await restoreChatHistory(token, stored);

        setThreadId(restored.threadId);
        setPendingEventId(restored.pendingEventId);

        if (restored.messages.length > 0) {
          setForceScrollNext(true);
          setMessages(restored.messages);
        } else {
          setMessages(starterMessages);
        }
      } catch (error) {
        const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
        if (authExpired) {
          await signOut();
        }
      } finally {
        setIsBootstrapping(false);
      }
    };

    void restoreSession();
  }, [token, allowed, signOut, starterMessages]);

  useEffect(() => {
    if (isBootstrapping) return;
    const stored: StoredChatSession = {
      threadId,
      pendingEventId,
    };
    void saveChatSession(stored);
  }, [threadId, pendingEventId, isBootstrapping]);

  const header = useMemo(
    () => (
      <View style={styles.header}>
        <Text style={styles.kicker}>Chat</Text>
        <Text style={styles.title}>Ask your memory</Text>
        <Text style={styles.subtitle}>Chat with your LLM in a calm, chat-first space.</Text>
      </View>
    ),
    [],
  );

  const sendMessage = async (overrideMessage?: string) => {
    const draft = overrideMessage ?? input;
    const trimmed = draft.trim();
    if (!trimmed || isSending || !allowed || isBootstrapping) return;
    setInput('');
    const pendingId = `${Date.now()}-pending`;

    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-user`, role: 'user', content: trimmed },
      { id: pendingId, role: 'assistant', content: 'Thinking...', pending: true },
    ]);

    setIsSending(true);
    try {
      const payload = {
        question: trimmed,
        thread_id: threadId,
        pending_event_id: pendingEventId ?? undefined,
      };
      const response = await apiFetch('/mobile/ask', {
        method: 'POST',
        body: JSON.stringify(payload),
        token,
      });

      setThreadId(response.thread_id ?? threadId);
      const commandResult = response.command_result as CommandResult | undefined;
      const assistantContent = commandResult
        ? commandResult.type === 'clarification_needed'
          ? 'I need a few more details to log that event.'
          : 'Here is the event proposal.'
        : response.answer ?? 'Ready when you are.';

      if (response.pending_event_id !== undefined) {
        setPendingEventId(response.pending_event_id ?? null);
      }

      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? {
                ...message,
                content: assistantContent,
                pending: false,
                metadata: commandResult ? { command_result: commandResult } : undefined,
              }
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

  const trimmedInput = input.trimStart();
  const hasCommandToken = /^\/\w+\s/.test(trimmedInput);
  const showSlashPalette = trimmedInput.startsWith('/') && !hasCommandToken;
  const slashQuery = trimmedInput.slice(1).split(/\s/)[0];

  useEffect(() => {
    if (!listRef.current || listHeight === 0 || (!isAtBottom && !forceScrollNext)) return;

    const padding = listHeight * 0.1;
    const hasTallMessage = lastMessageHeight > listHeight;
    const fallbackOffset = Math.max(0, contentHeight - listHeight);
    const tallMessageOffset = Math.max(0, contentHeight - lastMessageHeight - padding);
    const offset = hasTallMessage ? tallMessageOffset : fallbackOffset;

    requestAnimationFrame(() => {
      listRef.current?.scrollToOffset({ offset, animated: true });
    });
    if (forceScrollNext) {
      setForceScrollNext(false);
    }
  }, [messages.length, contentHeight, listHeight, lastMessageHeight, isAtBottom, forceScrollNext]);

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={keyboardBehavior}
        keyboardVerticalOffset={Platform.OS === 'ios' ? insets.top : 0}
      >
        <FlatList
          ref={listRef}
          style={styles.list}
          data={messages}
          keyExtractor={(item) => item.id}
          ListHeaderComponent={header}
          contentContainerStyle={[
            styles.listContent,
            {
              paddingTop: insets.top + 16,
              paddingBottom: 24,
            },
          ]}
          onLayout={(event) => {
            setListHeight(event.nativeEvent.layout.height);
          }}
          onContentSizeChange={(_, height) => {
            setContentHeight(height);
          }}
          onScroll={(event) => {
            const { contentOffset, layoutMeasurement, contentSize } = event.nativeEvent;
            const distanceFromBottom =
              contentSize.height - (contentOffset.y + layoutMeasurement.height);
            setIsAtBottom(distanceFromBottom < 48);
          }}
          scrollEventThrottle={16}
          renderItem={({ item }) => (
            <View
              style={[
                styles.messageBubble,
                item.role === 'user' ? styles.userBubble : styles.assistantBubble,
              ]}
              onLayout={(event) => {
                if (item.id === messages[messages.length - 1]?.id) {
                  setLastMessageHeight(event.nativeEvent.layout.height);
                }
              }}
            >
              <Text
                style={[
                  styles.messageText,
                  item.role === 'user' ? styles.userText : styles.assistantText,
                ]}
                selectable
              >
                {item.content}
              </Text>
              {item.metadata?.command_result?.type === 'event_confirmation' && (
                <View style={styles.commandCardWrap}>
                  <EventProposalCard
                    data={item.metadata.command_result as EventConfirmationData}
                    isSubmitting={isConfirmingEvent}
                    onConfirm={async (previewId) => {
                      if (isConfirmingEvent) return;
                      setIsConfirmingEvent(true);
                      try {
                        const result = await apiFetch('/mobile/commands/event/confirm', {
                          method: 'POST',
                          body: JSON.stringify({
                            preview_id: previewId,
                            confirmed: true,
                            modifications: {},
                            skip_entities: {},
                          }),
                          token,
                        });

                        const createdCount =
                          (result?.created_contacts?.length ?? 0) +
                          (result?.created_places?.length ?? 0);
                        const successMessage: Message = {
                          id: `${Date.now()}-event-success`,
                          role: 'assistant',
                          content: `Event created. ${createdCount > 0 ? `Created ${createdCount} new entities.` : ''}`,
                        };
                        setMessages((prev) => [...prev, successMessage]);
                        setPendingEventId(null);
                      } catch (error) {
                        console.error('Failed to create event:', error);
    setForceScrollNext(true);
    setMessages((prev) => [
                          ...prev,
                          {
                            id: `${Date.now()}-event-error`,
                            role: 'assistant',
                            content: 'I hit a snag creating that event. Try again in a moment.',
                          },
                        ]);
                      } finally {
                        setIsConfirmingEvent(false);
                      }
                    }}
                    onCancel={async (previewId) => {
                      if (isConfirmingEvent) return;
                      setIsConfirmingEvent(true);
                      try {
                        await apiFetch('/mobile/commands/event/confirm', {
                          method: 'POST',
                          body: JSON.stringify({
                            preview_id: previewId,
                            confirmed: false,
                          }),
                          token,
                        });
                        setMessages((prev) => [
                          ...prev,
                          {
                            id: `${Date.now()}-event-cancel`,
                            role: 'assistant',
                            content: 'Event creation canceled.',
                          },
                        ]);
                        setPendingEventId(null);
                      } catch {
                        setMessages((prev) => [
                          ...prev,
                          {
                            id: `${Date.now()}-event-cancel-error`,
                            role: 'assistant',
                            content: 'I could not cancel the event just now.',
                          },
                        ]);
                      } finally {
                        setIsConfirmingEvent(false);
                      }
                    }}
                  />
                </View>
              )}
              {item.metadata?.command_result?.type === 'clarification_needed' && (
                <View style={styles.commandCardWrap}>
                  <EventClarificationCard
                    data={item.metadata.command_result as EventClarificationData}
                    onSubmit={(answer) => {
                      const clarificationId =
                        (item.metadata?.command_result as EventClarificationData).clarification_id;
                      const clarificationToken = clarificationId
                        ? `\n\n[clarification_id:${clarificationId}]`
                        : '';
                      const combinedMessage = `/event ${
                        (item.metadata?.command_result as EventClarificationData).original_message
                      }\n\nAdditional details: ${answer}${clarificationToken}`;
                      void sendMessage(combinedMessage);
                    }}
                  />
                </View>
              )}
            </View>
          )}
        />
        {showSlashPalette && (
          <SlashCommandPalette
            query={slashQuery}
            onSelect={(command) => {
              const nextInput = `/${command} `;
              setInput(nextInput);
            }}
          />
        )}

        <View
          style={[
            styles.composer,
            {
              paddingBottom: 14 + (Platform.OS === 'ios' ? insets.bottom : 0),
            },
          ]}
        >
          <TextInput
            value={input}
            editable={allowed}
            style={[
              styles.input,
              !allowed && {
                backgroundColor: '#eee',
              },
            ]}
            onChangeText={setInput}
            placeholder="Send a message..."
            multiline
          />
          <Button
            label={isSending ? '...' : 'Send'}
            onPress={() => sendMessage()}
            disabled={!canSend}
            variant="primary"
            style={styles.sendButton}
          />
        </View>
      </KeyboardAvoidingView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  screen: {
    flex: 1,
  },
  list: {
    flex: 1,
  },
  listContent: {
    paddingHorizontal: 20,
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
    lineHeight: 24,
    paddingTop: 2,
    paddingBottom: 2,
  },
  userText: {
    color: '#fff',
  },
  assistantText: {
    color: theme.colors.ink,
  },
  commandCardWrap: {
    marginTop: 12,
  },
  composer: {
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
});
