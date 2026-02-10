import { LinearGradient } from 'expo-linear-gradient';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  KeyboardAvoidingViewProps,
  Platform,
  Pressable,
  Linking,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import type { BottomTabNavigationProp } from '@react-navigation/bottom-tabs';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { theme } from '@/theme';
import { UiDirectiveCard } from '@/components/ui-directive-card';
import { SlashCommandPalette } from '@/components/SlashCommandPalette';
import { loadChatSession, saveChatSession, StoredChatSession } from '@/chat/session';
import { restoreChatHistory } from '@/chat/threads';
import type { CommandResult as ThreadCommandResult } from '@/chat/threads';
import type { UiDirectiveBlock, UiDirectives, UiSubmissionInput } from '@/chat/uiDirectives';
import { getClientContext } from '@/location/clientContext';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
  };
};

type CommandResult = ThreadCommandResult;

type AskResponse = {
  answer?: string;
  thread_id?: string | null;
  pending_event_id?: string | null;
  command_result?: CommandResult;
  ui_directives?: UiDirectives;
};

type SendMessageInput =
  | string
  | {
      text?: string;
      uiSubmission?: UiSubmissionInput;
    };

type EventConfirmationResponse = {
  event_id?: string;
  created_contacts?: { contact_id: string; display_name: string }[];
  created_places?: { place_id: string; name: string }[];
};

type HomeTabParamList = {
  index: undefined;
  contacts: undefined;
  brain:
    | {
        sendEnabled?: boolean;
        isSending?: boolean;
      }
    | undefined;
};

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

const INLINE_MARKDOWN_PATTERN =
  /(\[[^\]]+\]\((?:https?:\/\/|mailto:|www\.)[^)\s]+\)|(?:https?:\/\/|mailto:|www\.)\S+|\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
const MARKDOWN_LINK_PATTERN = /^\[([^\]]+)\]\(([^)\s]+)\)$/;
const URL_TOKEN_PATTERN = /^(?:https?:\/\/|mailto:|www\.)\S+$/;
const TRAILING_URL_PUNCTUATION_PATTERN = /[),.!?;:]+$/;
const BULLET_LINE_PATTERN = /^[-*]\s+/;
const NUMBERED_LINE_PATTERN = /^(\d+)\.\s+(.*)$/;
const BLOCKQUOTE_LINE_PATTERN = /^>\s+/;
const EVENT_CONFIRM_ACTION_ID = 'event_confirmation_action';
const EVENT_CLARIFICATION_ACTION_PREFIX = 'event_clarification_submit';

function formatFieldLabel(fieldId: string): string {
  return fieldId
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\w/, (char) => char.toUpperCase());
}

function toSubmissionTextValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value
      .map((item) => toSubmissionTextValue(item))
      .filter(Boolean)
      .join(', ');
  }
  return '';
}

function fieldForSubmission(block: UiDirectiveBlock | undefined, fieldId: string) {
  const fields = block?.fields || [];
  return fields.find((field) => field.id === fieldId);
}

function optionLabelForField(field: ReturnType<typeof fieldForSubmission>, rawValue: string): string {
  const options = field?.options || [];
  const match = options.find((option) => option.id === rawValue);
  return match?.label || rawValue;
}

function buildEventClarificationAnswer(
  submission: UiSubmissionInput,
  directives: UiDirectives | undefined,
): string {
  const values = submission.values || {};
  const entries = Object.entries(values)
    .map(([key, value]) => [key, toSubmissionTextValue(value)] as const)
    .filter(([, value]) => Boolean(value));

  if (entries.length === 0) {
    return '';
  }

  const block = directives?.blocks?.find((candidate) => candidate.id === submission.block_id);
  const lines: string[] = [];

  for (const [key, value] of entries) {
    const field = fieldForSubmission(block, key);
    const label = field?.label || formatFieldLabel(key);
    const normalizedValue = optionLabelForField(field, value);
    const lowerKey = key.toLowerCase();
    if (
      lowerKey === 'details' ||
      lowerKey === 'description' ||
      lowerKey === 'summary' ||
      lowerKey === 'what_happened'
    ) {
      lines.push(normalizedValue);
      continue;
    }
    lines.push(`${label}: ${normalizedValue}`);
  }

  if (lines.length === 0 && submission.text_fallback?.trim()) {
    return submission.text_fallback.trim();
  }

  return lines.join('\n');
}

function parseEventChoice(optionIdRaw: unknown): { confirmed: boolean; previewId: string } | null {
  if (typeof optionIdRaw !== 'string') return null;
  const optionId = optionIdRaw.trim();
  if (optionId.startsWith('confirm:')) {
    const previewId = optionId.slice('confirm:'.length).trim();
    if (previewId) {
      return { confirmed: true, previewId };
    }
  }
  if (optionId.startsWith('cancel:')) {
    const previewId = optionId.slice('cancel:'.length).trim();
    if (previewId) {
      return { confirmed: false, previewId };
    }
  }
  return null;
}

function clarificationIdFromAction(actionIdRaw: string | undefined): string | null {
  if (!actionIdRaw) return null;
  const actionId = actionIdRaw.trim();
  if (!actionId.startsWith(`${EVENT_CLARIFICATION_ACTION_PREFIX}:`)) {
    return null;
  }
  const clarificationId = actionId
    .slice(`${EVENT_CLARIFICATION_ACTION_PREFIX}:`.length)
    .trim();
  return clarificationId || null;
}

function normalizeLinkUrl(url: string) {
  const trimmed = url.trim();
  if (trimmed.startsWith('www.')) {
    return `https://${trimmed}`;
  }
  return trimmed;
}

async function openMarkdownLink(rawUrl: string) {
  const url = normalizeLinkUrl(rawUrl);
  try {
    await Linking.openURL(url);
  } catch (error) {
    console.warn('Failed to open markdown link', error);
  }
}

function splitTrailingUrlPunctuation(token: string) {
  const trailing = token.match(TRAILING_URL_PUNCTUATION_PATTERN)?.[0] ?? '';
  if (!trailing) {
    return { url: token, trailingText: '' };
  }
  return {
    url: token.slice(0, -trailing.length),
    trailingText: trailing,
  };
}

function renderInlineMarkdown(text: string, keyPrefix: string) {
  const parts = text.split(INLINE_MARKDOWN_PATTERN).filter(Boolean);
  return parts.map((part, index) => {
    const markdownLinkMatch = part.match(MARKDOWN_LINK_PATTERN);
    if (markdownLinkMatch) {
      const [, label, rawUrl] = markdownLinkMatch;
      return (
        <Text
          key={`${keyPrefix}-link-${index}`}
          style={styles.markdownLink}
          accessibilityRole="link"
          selectable={false}
          onPress={() => {
            void openMarkdownLink(rawUrl);
          }}
        >
          {label}
        </Text>
      );
    }

    if (URL_TOKEN_PATTERN.test(part)) {
      const { url, trailingText } = splitTrailingUrlPunctuation(part);
      return (
        <React.Fragment key={`${keyPrefix}-url-${index}`}>
          <Text
            style={styles.markdownLink}
            accessibilityRole="link"
            selectable={false}
            onPress={() => {
              void openMarkdownLink(url);
            }}
          >
            {url}
          </Text>
          {trailingText ? <Text selectable>{trailingText}</Text> : null}
        </React.Fragment>
      );
    }

    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <Text key={`${keyPrefix}-bold-${index}`} style={styles.markdownBold} selectable>
          {part.slice(2, -2)}
        </Text>
      );
    }
    if (part.startsWith('*') && part.endsWith('*')) {
      return (
        <Text key={`${keyPrefix}-italic-${index}`} style={styles.markdownItalic} selectable>
          {part.slice(1, -1)}
        </Text>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <Text key={`${keyPrefix}-code-${index}`} style={styles.markdownInlineCode} selectable>
          {part.slice(1, -1)}
        </Text>
      );
    }
    return (
      <Text key={`${keyPrefix}-text-${index}`} selectable>
        {part}
      </Text>
    );
  });
}

function flushCodeBlock(
  blocks: React.ReactNode[],
  codeLines: string[],
  keyPrefix: string,
  codeBlockCount: number,
) {
  blocks.push(
    <View key={`${keyPrefix}-code-block-${codeBlockCount}`} style={styles.markdownCodeBlock}>
      <Text style={styles.markdownCodeText} selectable>
        {codeLines.join('\n')}
      </Text>
    </View>,
  );
}

function renderAssistantMarkdown(markdown: string, keyPrefix: string) {
  const blocks: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeBlockCount = 0;
  let codeLines: string[] = [];

  markdown.split('\n').forEach((line, index) => {
    const trimmedLine = line.trim();

    if (trimmedLine.startsWith('```')) {
      if (inCodeBlock) {
        flushCodeBlock(blocks, codeLines, keyPrefix, codeBlockCount);
        codeLines = [];
        inCodeBlock = false;
        codeBlockCount += 1;
      } else {
        inCodeBlock = true;
      }
      return;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    if (!trimmedLine) {
      blocks.push(<View key={`${keyPrefix}-space-${index}`} style={styles.markdownSpacer} />);
      return;
    }

    if (line.startsWith('### ')) {
      blocks.push(
        <Text key={`${keyPrefix}-h3-${index}`} style={styles.markdownH3} selectable>
          {renderInlineMarkdown(line.replace('### ', ''), `${keyPrefix}-h3-${index}`)}
        </Text>,
      );
      return;
    }

    if (line.startsWith('## ')) {
      blocks.push(
        <Text key={`${keyPrefix}-h2-${index}`} style={styles.markdownH2} selectable>
          {renderInlineMarkdown(line.replace('## ', ''), `${keyPrefix}-h2-${index}`)}
        </Text>,
      );
      return;
    }

    if (line.startsWith('# ')) {
      blocks.push(
        <Text key={`${keyPrefix}-h1-${index}`} style={styles.markdownH1} selectable>
          {renderInlineMarkdown(line.replace('# ', ''), `${keyPrefix}-h1-${index}`)}
        </Text>,
      );
      return;
    }

    if (BULLET_LINE_PATTERN.test(line)) {
      blocks.push(
        <View key={`${keyPrefix}-bullet-${index}`} style={styles.markdownListRow}>
          <Text style={styles.markdownListMarker} selectable>
            •
          </Text>
          <Text style={styles.markdownListText} selectable>
            {renderInlineMarkdown(
              line.replace(BULLET_LINE_PATTERN, ''),
              `${keyPrefix}-bullet-${index}`,
            )}
          </Text>
        </View>,
      );
      return;
    }

    const numberedMatch = line.match(NUMBERED_LINE_PATTERN);
    if (numberedMatch) {
      blocks.push(
        <View key={`${keyPrefix}-numbered-${index}`} style={styles.markdownListRow}>
          <Text style={styles.markdownListMarker} selectable>
            {numberedMatch[1]}.
          </Text>
          <Text style={styles.markdownListText} selectable>
            {renderInlineMarkdown(numberedMatch[2], `${keyPrefix}-numbered-${index}`)}
          </Text>
        </View>,
      );
      return;
    }

    if (BLOCKQUOTE_LINE_PATTERN.test(line)) {
      blocks.push(
        <View key={`${keyPrefix}-quote-${index}`} style={styles.markdownQuote}>
          <Text style={styles.markdownQuoteText} selectable>
            {renderInlineMarkdown(
              line.replace(BLOCKQUOTE_LINE_PATTERN, ''),
              `${keyPrefix}-quote-${index}`,
            )}
          </Text>
        </View>,
      );
      return;
    }

    blocks.push(
      <Text key={`${keyPrefix}-paragraph-${index}`} style={styles.markdownParagraph} selectable>
        {renderInlineMarkdown(line, `${keyPrefix}-paragraph-${index}`)}
      </Text>,
    );
  });

  if (codeLines.length > 0) {
    flushCodeBlock(blocks, codeLines, keyPrefix, codeBlockCount);
  }

  return blocks;
}

export default function ChatScreen() {
  const { token, signOut, email } = useAuth();
  const insets = useSafeAreaInsets();
  const tabBarHeight = useBottomTabBarHeight();
  const listRef = useRef<FlatList<Message>>(null);
  const navigation = useNavigation<BottomTabNavigationProp<HomeTabParamList, 'brain'>>();
  const isFocused = useIsFocused();
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
  const [composerHeight, setComposerHeight] = useState(0);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const listBottomInset = insets.bottom + 24;

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
    navigation.setParams({ sendEnabled: canSend, isSending });
  }, [navigation, canSend, isSending]);

  useEffect(() => {
    if (isBootstrapping) return;
    const stored: StoredChatSession = {
      threadId,
      pendingEventId,
    };
    void saveChatSession(stored);
  }, [threadId, pendingEventId, isBootstrapping]);

  useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showListener = Keyboard.addListener(showEvent, () => {
      setKeyboardVisible(true);
    });
    const hideListener = Keyboard.addListener(hideEvent, () => {
      setKeyboardVisible(false);
    });

    return () => {
      showListener.remove();
      hideListener.remove();
    };
  }, []);

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

  const sendMessage = useCallback(async (override?: SendMessageInput) => {
    const overrideText = typeof override === 'string' ? override : override?.text;
    const uiSubmission = typeof override === 'string' ? undefined : override?.uiSubmission;

    const draft = overrideText ?? input;
    const trimmed = draft.trim();
    const outboundText =
      trimmed || uiSubmission?.text_fallback?.trim() || 'Submitted structured response.';

    if (!outboundText || isSending || !allowed || isBootstrapping) return;
    setInput('');
    const pendingId = `${Date.now()}-pending`;

    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-user`, role: 'user', content: outboundText },
      { id: pendingId, role: 'assistant', content: 'Thinking...', pending: true },
    ]);

    setIsSending(true);
    try {
      const payload = {
        question: outboundText,
        thread_id: threadId,
        pending_event_id: pendingEventId ?? undefined,
        client_context: getClientContext(),
        ui_submission: uiSubmission ?? undefined,
      };
      const response = (await apiFetch('/mobile/ask', {
        method: 'POST',
        body: JSON.stringify(payload),
        token,
      })) as AskResponse;

      setThreadId(response.thread_id ?? threadId);
      const commandResult = response.command_result as CommandResult | undefined;
      const uiDirectives = response.ui_directives;
      const assistantContent =
        response.answer ??
        uiDirectives?.fallback_text ??
        (commandResult ? 'Command completed.' : 'Ready when you are.');

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
                metadata:
                  commandResult || uiDirectives
                    ? {
                        command_result: commandResult,
                        ui_directives: uiDirectives,
                      }
                    : undefined,
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
  }, [allowed, input, isBootstrapping, isSending, pendingEventId, signOut, threadId, token]);

  const handleDirectiveSubmission = useCallback(
    async (messageId: string, directives: UiDirectives | undefined, submission: UiSubmissionInput) => {
      if (submission.action_id === EVENT_CONFIRM_ACTION_ID) {
        const choice = parseEventChoice(submission.values?.['option_id']);
        if (!choice || isConfirmingEvent) {
          return;
        }

        setIsConfirmingEvent(true);
        try {
          const result = (await apiFetch('/mobile/commands/event/confirm', {
            method: 'POST',
            body: JSON.stringify(
              choice.confirmed
                ? {
                    preview_id: choice.previewId,
                    confirmed: true,
                    modifications: {},
                    skip_entities: {},
                  }
                : {
                    preview_id: choice.previewId,
                    confirmed: false,
                  },
            ),
            token,
          })) as EventConfirmationResponse;

          setPendingEventId(null);
          setMessages((prev) =>
            prev.map((message) =>
              message.id === messageId
                ? {
                    ...message,
                    metadata: message.metadata
                      ? {
                          ...message.metadata,
                          ui_directives: undefined,
                        }
                      : undefined,
                  }
                : message,
            ),
          );

          if (choice.confirmed) {
            const createdCount =
              (result?.created_contacts?.length ?? 0) + (result?.created_places?.length ?? 0);
            setMessages((prev) => [
              ...prev,
              {
                id: `${Date.now()}-event-success`,
                role: 'assistant',
                content: `Event created.${createdCount > 0 ? ` Created ${createdCount} new entities.` : ''}`,
              },
            ]);
          } else {
            setMessages((prev) => [
              ...prev,
              {
                id: `${Date.now()}-event-cancel`,
                role: 'assistant',
                content: 'Event creation canceled.',
              },
            ]);
          }
          setForceScrollNext(true);
          return;
        } catch {
          setMessages((prev) => [
            ...prev,
            {
              id: `${Date.now()}-event-action-error`,
              role: 'assistant',
              content: 'I could not complete that event action right now.',
            },
          ]);
          setForceScrollNext(true);
          return;
        } finally {
          setIsConfirmingEvent(false);
        }
      }

      if (submission.action_id?.startsWith(EVENT_CLARIFICATION_ACTION_PREFIX)) {
        const answer = buildEventClarificationAnswer(submission, directives);
        if (!answer) {
          return;
        }
        const clarificationId = clarificationIdFromAction(submission.action_id);
        const clarificationToken = clarificationId ? `\n\n[clarification_id:${clarificationId}]` : '';
        const combinedMessage = `/event ${answer}${clarificationToken}`;
        void sendMessage(combinedMessage);
        return;
      }

      const fallbackText =
        submission.text_fallback?.trim() ||
        directives?.fallback_text ||
        'Submitted structured response.';
      void sendMessage({
        text: fallbackText,
        uiSubmission: submission,
      });
    },
    [isConfirmingEvent, sendMessage, token],
  );

  useEffect(() => {
    const unsubscribe = navigation.addListener('tabPress', (event) => {
      if (!isFocused) return;
      event.preventDefault();
      if (!canSend) return;
      void sendMessage();
    });

    return unsubscribe;
  }, [navigation, isFocused, canSend, sendMessage]);

  const trimmedInput = input.trimStart();
  const hasCommandToken = /^\/\w+\s/.test(trimmedInput);
  const showSlashPalette = trimmedInput.startsWith('/') && !hasCommandToken;
  const slashQuery = trimmedInput.slice(1).split(/\s/)[0];
  const showAnchoredSlashPalette = showSlashPalette && composerHeight > 0;

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
        keyboardVerticalOffset={0}
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
              paddingBottom: listBottomInset,
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
              {item.role === 'assistant' ? (
                <View style={styles.markdownContainer}>
                  {renderAssistantMarkdown(item.content, item.id)}
                </View>
              ) : (
                <Text style={[styles.messageText, styles.userText]} selectable>
                  {item.content}
                </Text>
              )}
              {item.metadata?.ui_directives && (
                <View style={styles.commandCardWrap}>
                  <UiDirectiveCard
                    directives={item.metadata.ui_directives}
                    isSubmitting={isSending || isConfirmingEvent}
                    onSubmit={(submission) => {
                      void handleDirectiveSubmission(
                        item.id,
                        item.metadata?.ui_directives,
                        submission,
                      );
                    }}
                  />
                </View>
              )}
            </View>
          )}
        />
        {showAnchoredSlashPalette && (
          <View style={[styles.slashPaletteAnchor, { bottom: composerHeight + 8 }]}>
            <SlashCommandPalette
              query={slashQuery}
              onSelect={(command) => {
                const nextInput = `/${command} `;
                setInput(nextInput);
              }}
            />
          </View>
        )}

        <View
          onLayout={(event) => {
            setComposerHeight(event.nativeEvent.layout.height);
          }}
          style={[
            styles.composer,
            {
              paddingBottom: (keyboardVisible ? 12 : insets.bottom + tabBarHeight) + 12,
              paddingRight: keyboardVisible ? 12 : 16,
              gap: keyboardVisible ? 8 : 10,
            },
          ]}
        >
          <View style={styles.inputWrap}>
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
          </View>
          {keyboardVisible ? (
            <Pressable
              onPress={() => sendMessage()}
              disabled={!canSend}
              style={({ pressed }) => [
                styles.inlineSendButton,
                pressed && styles.inlineSendButtonPressed,
                !canSend && styles.inlineSendButtonDisabled,
              ]}
            >
              {isSending ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : (
                <Ionicons name="send" size={18} color="#fff" />
              )}
            </Pressable>
          ) : null}
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
    marginTop: 0,
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
  markdownContainer: {
    gap: 6,
  },
  userText: {
    color: '#fff',
  },
  markdownH1: {
    fontSize: 18,
    lineHeight: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownH2: {
    fontSize: 16,
    lineHeight: 24,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownH3: {
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  markdownParagraph: {
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.ink,
  },
  markdownListRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  markdownListMarker: {
    minWidth: 16,
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.ink,
    fontWeight: '600',
  },
  markdownListText: {
    flex: 1,
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.ink,
  },
  markdownQuote: {
    borderLeftWidth: 3,
    borderLeftColor: theme.colors.line,
    paddingLeft: 10,
    paddingVertical: 2,
  },
  markdownQuoteText: {
    fontSize: 15,
    lineHeight: 24,
    color: theme.colors.mutedInk,
  },
  markdownCodeBlock: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#F5F7FA',
    borderRadius: theme.radius.md,
    paddingVertical: 10,
    paddingHorizontal: 12,
  },
  markdownCodeText: {
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
    fontSize: 13,
    lineHeight: 20,
    color: theme.colors.ink,
  },
  markdownInlineCode: {
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
    fontSize: 14,
    lineHeight: 22,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#F5F7FA',
    borderRadius: theme.radius.md,
    paddingHorizontal: 4,
    color: theme.colors.ink,
  },
  markdownLink: {
    color: theme.colors.accentDeep,
    textDecorationLine: 'underline',
  },
  markdownBold: {
    fontWeight: '700',
  },
  markdownItalic: {
    fontStyle: 'italic',
  },
  markdownSpacer: {
    height: 6,
  },
  commandCardWrap: {
    marginTop: 12,
  },
  slashPaletteAnchor: {
    position: 'absolute',
    left: 0,
    right: 0,
    zIndex: 3,
    elevation: 3,
  },
  composer: {
    paddingHorizontal: 16,
    paddingVertical: 14,
    backgroundColor: 'transparent',
    flexDirection: 'row',
    gap: 10,
    alignItems: 'center',
  },
  inputWrap: {
    flex: 1,
  },
  input: {
    flex: 1,
    minHeight: 46,
    maxHeight: 120,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    color: theme.colors.ink,
    textAlignVertical: 'center',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 8 },
    elevation: 3,
  },
  inlineSendButton: {
    alignSelf: 'flex-end',
    width: 46,
    height: 46,
    borderRadius: 23,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.2,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 8 },
    elevation: 4,
  },
  inlineSendButtonPressed: {
    opacity: 0.9,
    transform: [{ scale: 0.98 }],
  },
  inlineSendButtonDisabled: {
    opacity: 0.5,
  },
});
