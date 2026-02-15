import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  KeyboardEvent,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { useFocusEffect } from '@react-navigation/native';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';
import { UiDirectiveCard } from '@/components/ui-directive-card';
import { SlashCommandPalette } from '@/components/SlashCommandPalette';
import { renderAssistantMarkdown } from '@/components/MarkdownRenderer';
import type {
  EventContactOption,
  EventDraft,
  EventDraftModifications,
} from '@/components/event-draft/types';
import { loadChatSession, saveChatSession, StoredChatSession } from '@/chat/session';
import { restoreChatHistory } from '@/chat/threads';
import type { CommandResult as ThreadCommandResult, EventResolvedStatus } from '@/chat/threads';
import type { UiDirectiveBlock, UiDirectives, UiSubmissionInput } from '@/chat/uiDirectives';
import {
  clearEventDraftEditSession,
  consumeEventDraftEditResult,
  createEventDraftEditSession,
} from '@/events/draftEditorSession';
import { getClientContext } from '@/location/clientContext';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
    event_resolved?: EventResolvedStatus;
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

type EventAction = {
  type: 'confirm' | 'cancel' | 'edit';
  previewId: string;
};

type EventCommandResultPayload = {
  type?: string;
  preview_id?: string;
  extracted?: {
    title?: unknown;
    summary?: unknown;
    when?: unknown;
    where?: unknown;
    tags?: unknown;
    types?: unknown;
  };
  resolution?: {
    contacts?: { contact_id?: unknown; display_name?: unknown }[];
    new_entities?: {
      contacts?: { display_name?: unknown; contact_id?: unknown }[];
    };
  };
};

const EVENT_CONFIRM_ACTION_ID = 'event_confirmation_action';
const EVENT_CLARIFICATION_ACTION_PREFIX = 'event_clarification_submit';
const EVENT_CONFIRM_OPTION_PREFIX = 'confirm:';
const EVENT_CANCEL_OPTION_PREFIX = 'cancel:';
const EVENT_EDIT_OPTION_PREFIX = 'edit:';
const MIN_CHAT_INPUT_HEIGHT = 46;
const MAX_CHAT_INPUT_HEIGHT = 120;
const COMPOSER_KEYBOARD_GAP = 20;

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

function parseEventAction(optionIdRaw: unknown): EventAction | null {
  if (typeof optionIdRaw !== 'string') return null;
  const optionId = optionIdRaw.trim();
  if (optionId.startsWith(EVENT_CONFIRM_OPTION_PREFIX)) {
    const previewId = optionId.slice(EVENT_CONFIRM_OPTION_PREFIX.length).trim();
    if (previewId) {
      return { type: 'confirm', previewId };
    }
  }
  if (optionId.startsWith(EVENT_EDIT_OPTION_PREFIX)) {
    const previewId = optionId.slice(EVENT_EDIT_OPTION_PREFIX.length).trim();
    if (previewId) {
      return { type: 'edit', previewId };
    }
  }
  if (optionId.startsWith(EVENT_CANCEL_OPTION_PREFIX)) {
    const previewId = optionId.slice(EVENT_CANCEL_OPTION_PREFIX.length).trim();
    if (previewId) {
      return { type: 'cancel', previewId };
    }
  }
  return null;
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  return String(value).trim();
}

function stringArrayValue(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => textValue(entry))
    .filter(Boolean);
}

function normalizedDraftValue(value: string) {
  return value.trim();
}

function extractEventPreviewId(commandResult: CommandResult | undefined): string | null {
  if (!commandResult || typeof commandResult !== 'object') return null;
  const payload = commandResult as EventCommandResultPayload;
  const previewId = textValue(payload.preview_id);
  return previewId || null;
}

function buildEventDraft(commandResult: CommandResult | undefined, previewId: string): EventDraft | null {
  if (!commandResult || typeof commandResult !== 'object') return null;
  const payload = commandResult as EventCommandResultPayload;
  const payloadPreviewId = textValue(payload.preview_id);
  if (payloadPreviewId !== previewId) return null;
  const extracted = payload.extracted;
  if (!extracted || typeof extracted !== 'object') return null;
  const resolvedContacts = Array.isArray(payload.resolution?.contacts)
    ? payload.resolution?.contacts
    : [];
  const newEntityContacts = Array.isArray(payload.resolution?.new_entities?.contacts)
    ? payload.resolution?.new_entities?.contacts
    : [];

  const seenIds = new Set<string>();
  const participants: { contactId: string; displayName: string }[] = [];

  for (const contact of resolvedContacts) {
    const contactId = textValue(contact.contact_id);
    if (!contactId || seenIds.has(contactId)) continue;
    seenIds.add(contactId);
    participants.push({
      contactId,
      displayName: textValue(contact.display_name) || contactId,
    });
  }

  // Include new (not-yet-created) contacts so that edits don't silently drop
  // people detected by the LLM resolution. These use a synthetic placeholder ID
  // (prefixed with `new:`) that the backend will recognize and create on confirm.
  for (const newContact of newEntityContacts) {
    const displayName = textValue(newContact.display_name);
    if (!displayName) continue;
    // new_entities contacts don't have contact_id yet — use a stable placeholder
    const placeholderId = `new:${displayName}`;
    if (seenIds.has(placeholderId)) continue;
    seenIds.add(placeholderId);
    participants.push({
      contactId: placeholderId,
      displayName,
    });
  }

  return {
    title: textValue(extracted.title),
    summary: textValue(extracted.summary),
    when: textValue(extracted.when),
    where: textValue(extracted.where),
    tags: stringArrayValue(extracted.tags),
    types: stringArrayValue(extracted.types),
    participants,
  };
}

function applyDraftModifications(
  baseDraft: EventDraft,
  modifications: EventDraftModifications | undefined,
  contactNameById: Map<string, string>,
): EventDraft {
  if (!modifications) return baseDraft;
  const participantIds = modifications.contact_ids;
  const participants =
    participantIds === undefined
      ? baseDraft.participants
      : participantIds.map((contactId) => ({
          contactId,
          displayName: contactNameById.get(contactId) || contactId,
        }));

  return {
    title: modifications.title ?? baseDraft.title,
    summary: modifications.summary ?? baseDraft.summary,
    when:
      modifications.when === null
        ? ''
        : modifications.when === undefined
          ? baseDraft.when
          : modifications.when,
    where: modifications.where ?? baseDraft.where,
    tags: modifications.tags ?? baseDraft.tags,
    types: modifications.types ?? baseDraft.types,
    participants,
  };
}

function sameStringList(left: string[], right: string[]) {
  if (left.length !== right.length) return false;
  return left.every((entry, index) => entry === right[index]);
}

function buildDraftModifications(baseDraft: EventDraft, nextDraft: EventDraft): EventDraftModifications {
  const modifications: EventDraftModifications = {};

  if (normalizedDraftValue(baseDraft.title) !== normalizedDraftValue(nextDraft.title)) {
    modifications.title = normalizedDraftValue(nextDraft.title);
  }
  if (normalizedDraftValue(baseDraft.summary) !== normalizedDraftValue(nextDraft.summary)) {
    modifications.summary = normalizedDraftValue(nextDraft.summary);
  }
  if (normalizedDraftValue(baseDraft.when) !== normalizedDraftValue(nextDraft.when)) {
    modifications.when = normalizedDraftValue(nextDraft.when) || null;
  }
  if (normalizedDraftValue(baseDraft.where) !== normalizedDraftValue(nextDraft.where)) {
    modifications.where = normalizedDraftValue(nextDraft.where);
  }
  if (!sameStringList(baseDraft.tags, nextDraft.tags)) {
    modifications.tags = nextDraft.tags;
  }
  if (!sameStringList(baseDraft.types, nextDraft.types)) {
    modifications.types = nextDraft.types;
  }
  const baseParticipantIds = baseDraft.participants.map((participant) => participant.contactId);
  const nextParticipantIds = nextDraft.participants.map((participant) => participant.contactId);
  if (!sameStringList(baseParticipantIds, nextParticipantIds)) {
    modifications.contact_ids = nextParticipantIds;
  }

  return modifications;
}

function modificationSummary(modifications: EventDraftModifications): string {
  const labels: string[] = [];
  if ('title' in modifications) labels.push('title');
  if ('summary' in modifications) labels.push('summary');
  if ('when' in modifications) labels.push('when');
  if ('where' in modifications) labels.push('where');
  if ('tags' in modifications) labels.push('tags');
  if ('types' in modifications) labels.push('types');
  if ('contact_ids' in modifications) labels.push('participants');
  return labels.join(', ');
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

export default function ChatScreen() {
  const router = useRouter();
  const { token, signOut, email, isLoading: isAuthLoading } = useAuth();
  const insets = useSafeAreaInsets();
  const tabBarHeight = useBottomTabBarHeight();
  const listRef = useRef<FlatList<Message>>(null);
  const inputRef = useRef<TextInput>(null);
  const [input, setInput] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [isConfirmingEvent, setIsConfirmingEvent] = useState(false);
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  const [eventDraftModificationsByPreview, setEventDraftModificationsByPreview] = useState<
    Record<string, EventDraftModifications>
  >({});
  const [activeDraftEditorSessionId, setActiveDraftEditorSessionId] = useState<string | null>(null);
  const [eventEditorContacts, setEventEditorContacts] = useState<EventContactOption[]>([]);
  const isAtBottomRef = useRef(true);
  const [forceScrollNext, setForceScrollNext] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const hasHydratedSessionRef = useRef(false);
  const restoreGenerationRef = useRef(0);
  const [composerHeight, setComposerHeight] = useState(0);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const composerBottomOffset = keyboardVisible
    ? Platform.OS === 'ios'
      ? Math.max(0, keyboardHeight - insets.bottom) + COMPOSER_KEYBOARD_GAP
      : Math.max(0, keyboardHeight - insets.bottom) + 2*COMPOSER_KEYBOARD_GAP
    : 0;
  const listBottomInset =
    composerHeight > 0 ? composerHeight + 16 : insets.bottom + tabBarHeight + 80;

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
    let cancelled = false;
    const restoreGeneration = restoreGenerationRef.current + 1;
    restoreGenerationRef.current = restoreGeneration;

    const isCurrentRestore = () =>
      !cancelled && restoreGenerationRef.current === restoreGeneration;

    const restoreSession = async () => {
      if (isAuthLoading) {
        return;
      }

      if (!token || !allowed) {
        hasHydratedSessionRef.current = false;
        if (!isCurrentRestore()) return;
        setThreadId(null);
        setPendingEventId(null);
        setMessages(starterMessages);
        setIsBootstrapping(false);
        return;
      }

      if (hasHydratedSessionRef.current) {
        if (!isCurrentRestore()) return;
        setIsBootstrapping(false);
        return;
      }

      if (!isCurrentRestore()) return;
      setIsBootstrapping(true);
      try {
        const stored = await loadChatSession();
        if (!isCurrentRestore()) return;
        const restored = await restoreChatHistory(token, stored);
        if (!isCurrentRestore()) return;

        hasHydratedSessionRef.current = true;
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
        if (!isCurrentRestore()) return;
        setIsBootstrapping(false);
      }
    };

    void restoreSession();

    return () => {
      cancelled = true;
    };
  }, [isAuthLoading, token, allowed, signOut, starterMessages]);

  useEffect(() => {
    if (isBootstrapping || isAuthLoading) return;
    const stored: StoredChatSession = {
      threadId,
      pendingEventId,
    };
    void saveChatSession(stored);
  }, [threadId, pendingEventId, isBootstrapping, isAuthLoading]);

  useEffect(() => {
    if (!pendingEventId) {
      setEventDraftModificationsByPreview({});
      return;
    }

    setEventDraftModificationsByPreview((prev) => {
      if (!prev[pendingEventId]) return {};
      return { [pendingEventId]: prev[pendingEventId] };
    });
  }, [pendingEventId]);

  useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showListener = Keyboard.addListener(showEvent, (event: KeyboardEvent) => {
      setKeyboardVisible(true);
      setKeyboardHeight(event.endCoordinates?.height ?? 0);
    });
    const hideListener = Keyboard.addListener(hideEvent, () => {
      setKeyboardVisible(false);
      setKeyboardHeight(0);
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
        <Text style={styles.title}>Ask "your" memory</Text>
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
    Keyboard.dismiss();
    setInput('');
    setForceScrollNext(true);
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

      setThreadId((prev) => response.thread_id ?? prev);
      const commandResult = response.command_result as CommandResult | undefined;
      const uiDirectives = response.ui_directives;
      const assistantContent =
        response.answer ??
        uiDirectives?.fallback_text ??
        (commandResult ? 'Command completed.' : 'Ready when you are.');

      if (response.pending_event_id !== undefined) {
        setPendingEventId(response.pending_event_id ?? null);
      }

      setForceScrollNext(true);
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
        setForceScrollNext(true);
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
      setForceScrollNext(true);
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

  const loadEventEditorContacts = useCallback(async (): Promise<EventContactOption[]> => {
    if (!token) return [];
    if (eventEditorContacts.length > 0) return eventEditorContacts;

    try {
      const response = (await apiFetch('/mobile/contacts', { token })) as {
        contacts?: EventContactOption[];
      };
      const contacts = Array.isArray(response.contacts) ? response.contacts : [];
      setEventEditorContacts(contacts);
      return contacts;
    } catch {
      return [];
    }
  }, [eventEditorContacts, token]);

  const applyEventDraftEdits = useCallback(
    (previewId: string, baseDraft: EventDraft, nextDraft: EventDraft) => {
      const modifications = buildDraftModifications(baseDraft, nextDraft);
      const modifiedFields = modificationSummary(modifications);

      setEventDraftModificationsByPreview((prev) => {
        const next = { ...prev };
        if (modifiedFields) {
          next[previewId] = modifications;
        } else {
          delete next[previewId];
        }
        return next;
      });

      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-event-edit-status`,
          role: 'assistant',
          content: modifiedFields
            ? `Updated draft fields: ${modifiedFields}. Tap Create event when ready.`
            : 'No field changes were saved.',
        },
      ]);
      setForceScrollNext(true);
    },
    [],
  );

  useFocusEffect(
    useCallback(() => {
      if (!activeDraftEditorSessionId) {
        return () => undefined;
      }

      const result = consumeEventDraftEditResult(activeDraftEditorSessionId);
      if (result) {
        console.info('[event-draft-session] apply-on-focus', {
          sessionId: activeDraftEditorSessionId,
          previewId: result.previewId,
        });
        applyEventDraftEdits(result.previewId, result.baseDraft, result.nextDraft);
        setActiveDraftEditorSessionId(null);
      } else {
        console.info('[event-draft-session] no-result-on-focus', {
          sessionId: activeDraftEditorSessionId,
        });
      }

      return () => undefined;
    }, [activeDraftEditorSessionId, applyEventDraftEdits]),
  );

  const handleDirectiveSubmission = useCallback(
    async (
      messageId: string,
      directives: UiDirectives | undefined,
      submission: UiSubmissionInput,
      commandResult: CommandResult | undefined,
    ) => {
      if (submission.action_id === EVENT_CONFIRM_ACTION_ID) {
        const action = parseEventAction(submission.values?.['option_id']);
        if (!action || isConfirmingEvent) {
          return;
        }

        if (pendingEventId && action.previewId !== pendingEventId) {
          setMessages((prev) => [
            ...prev,
            {
              id: `${Date.now()}-event-superseded`,
              role: 'assistant',
              content: 'That draft is no longer active. Use the newest event preview card.',
            },
          ]);
          setForceScrollNext(true);
          return;
        }

        if (action.type === 'edit') {
          const loadedContacts = await loadEventEditorContacts();
          const contactNameById = new Map(
            loadedContacts.map((contact) => [contact.contact_id, contact.display_name]),
          );
          const baseDraft = buildEventDraft(commandResult, action.previewId);
          // Populate display names for new: placeholder participants from the base draft
          if (baseDraft) {
            for (const participant of baseDraft.participants) {
              if (!contactNameById.has(participant.contactId)) {
                contactNameById.set(participant.contactId, participant.displayName);
              }
            }
          }
          if (!baseDraft) {
            setMessages((prev) => [
              ...prev,
              {
                id: `${Date.now()}-event-edit-unavailable`,
                role: 'assistant',
                content: 'I could not load that draft for editing. Please retry from the latest event preview.',
              },
            ]);
            setForceScrollNext(true);
            return;
          }

          const existingModifications = eventDraftModificationsByPreview[action.previewId];
          const session = createEventDraftEditSession({
            previewId: action.previewId,
            baseDraft,
            initialDraft: applyDraftModifications(
              baseDraft,
              existingModifications,
              contactNameById,
            ),
            availableContacts: loadedContacts,
          });
          setActiveDraftEditorSessionId((previousSessionId) => {
            clearEventDraftEditSession(previousSessionId);
            return session.sessionId;
          });
          console.info('[event-draft-session] navigate-editor', {
            sessionId: session.sessionId,
            previewId: action.previewId,
          });
          router.push({
            pathname: '/events/[eventId]',
            params: {
              eventId: action.previewId,
              draftSessionId: session.sessionId,
            },
          });
          return;
        }

        setIsConfirmingEvent(true);
        try {
          const modifications = eventDraftModificationsByPreview[action.previewId] || {};
          const result = (await apiFetch('/mobile/commands/event/confirm', {
            method: 'POST',
            body: JSON.stringify(
              action.type === 'confirm'
                ? {
                    preview_id: action.previewId,
                    confirmed: true,
                    modifications,
                    skip_entities: {},
                  }
                : {
                    preview_id: action.previewId,
                    confirmed: false,
                  },
            ),
            token,
          })) as EventConfirmationResponse;

          setPendingEventId(null);
          setEventDraftModificationsByPreview((prev) => {
            if (!prev[action.previewId]) return prev;
            const next = { ...prev };
            delete next[action.previewId];
            return next;
          });
          const resolvedStatus: EventResolvedStatus =
            action.type === 'confirm' ? 'created' : 'cancelled';
          setMessages((prev) =>
            prev.map((message) =>
              message.id === messageId
                ? {
                    ...message,
                    metadata: {
                      ...message.metadata,
                      event_resolved: resolvedStatus,
                    },
                  }
                : message,
            ),
          );
          setForceScrollNext(true);
          return;
        } catch (error) {
          const detail = error instanceof Error ? error.message.toLowerCase() : '';
          const expired = detail.includes('not found or expired');
          setMessages((prev) => [
            ...prev,
            {
              id: `${Date.now()}-event-action-error`,
              role: 'assistant',
              content: expired
                ? 'This event draft expired. Please run /event again.'
                : 'I could not complete that event action right now.',
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
    [
      eventDraftModificationsByPreview,
      isConfirmingEvent,
      loadEventEditorContacts,
      pendingEventId,
      router,
      sendMessage,
      token,
    ],
  );

  const trimmedInput = input.trimStart();
  const hasCommandToken = /^\/\w+\s/.test(trimmedInput);
  const showSlashPalette = trimmedInput.startsWith('/') && !hasCommandToken;
  const slashQuery = trimmedInput.slice(1).split(/\s/)[0];
  const showAnchoredSlashPalette = showSlashPalette && composerHeight > 0;
  const lastMessage = messages[messages.length - 1];

  useEffect(() => {
    if (!listRef.current) return;
    if (!isAtBottomRef.current && !forceScrollNext) return;

    requestAnimationFrame(() => {
      listRef.current?.scrollToEnd({ animated: forceScrollNext });
    });
    if (forceScrollNext) {
      setForceScrollNext(false);
    }
  }, [
    lastMessage?.id,
    lastMessage?.content,
    lastMessage?.pending,
    listBottomInset,
    forceScrollNext,
  ]);

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={undefined}
        keyboardVerticalOffset={0}
      >
        <FlatList
          ref={listRef}
          style={styles.list}
          data={messages}
          keyExtractor={(item) => item.id}
          ListHeaderComponent={header}
          ListFooterComponent={<View style={{ height: listBottomInset }} />}
          contentContainerStyle={[
            styles.listContent,
            {
              paddingTop: insets.top + 16,
            },
          ]}
          onScroll={(event) => {
            const { contentOffset, layoutMeasurement, contentSize } = event.nativeEvent;
            const distanceFromBottom =
              contentSize.height - (contentOffset.y + layoutMeasurement.height);
            isAtBottomRef.current = distanceFromBottom < 48;
          }}
          scrollEventThrottle={16}
          renderItem={({ item }) => {
            const previewId = extractEventPreviewId(item.metadata?.command_result);
            const isSupersededEventCard = Boolean(
              previewId && pendingEventId && previewId !== pendingEventId,
            );

            return (
              <View
                style={[
                  styles.messageBubble,
                  item.role === 'user' ? styles.userBubble : styles.assistantBubble,
                ]}>
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
                      isSubmitting={isSending || isConfirmingEvent || isSupersededEventCard}
                      resolvedStatus={item.metadata.event_resolved}
                      onSubmit={(submission) => {
                        void handleDirectiveSubmission(
                          item.id,
                          item.metadata?.ui_directives,
                          submission,
                          item.metadata?.command_result,
                        );
                      }}
                    />
                    {isSupersededEventCard ? (
                      <Text style={styles.supersededNote}>
                        A newer event draft is active. This card is read-only.
                      </Text>
                    ) : null}
                  </View>
                )}
              </View>
            );
          }}
        />
        {showAnchoredSlashPalette && (
          <View style={[styles.slashPaletteAnchor, { bottom: composerHeight + composerBottomOffset + 8 }]}>
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
              bottom: composerBottomOffset,
              paddingBottom: (keyboardVisible ? 24 : insets.bottom + tabBarHeight + 8),
              paddingRight: 16,
              gap: 10,
            },
          ]}
        >
          <View style={styles.inputWrap}>
            <TextInput
              ref={inputRef}
              value={input}
              editable={allowed}
              style={[
                styles.input,
                {
                  minHeight: MIN_CHAT_INPUT_HEIGHT,
                  maxHeight: MAX_CHAT_INPUT_HEIGHT,
                  width: '100%',
                  paddingRight: 60,
                },
                !allowed && {
                  backgroundColor: '#eee',
                },
              ]}
              onChangeText={setInput}
              placeholder="Ask me anything..."
              placeholderTextColor="#A7AFB7"
              multiline
              onFocus={() => {
                setForceScrollNext(true);
              }}
              onBlur={() => {
                if (Platform.OS === 'android') {
                  setKeyboardVisible(false);
                  setKeyboardHeight(0);
                }
              }}
              scrollEnabled={true}
            />
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
                <Ionicons name="send" size={16} color="#fff" />
              )}
            </Pressable>
          </View>
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
    maxWidth: '90%',
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
  commandCardWrap: {
    marginTop: 12,
    gap: 8,
  },
  supersededNote: {
    fontSize: 12,
    lineHeight: 17,
    color: theme.colors.mutedInk,
  },
  slashPaletteAnchor: {
    position: 'absolute',
    left: 0,
    right: 0,
    zIndex: 3,
    elevation: 3,
  },
  composer: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 2,
    paddingHorizontal: 16,
    paddingVertical: 14,
    backgroundColor: 'transparent',
    flexDirection: 'row',
    gap: 10,
    alignItems: 'flex-end',
  },
  inputWrap: {
    flex: 1,
    position: 'relative',
  },
  input: {
    fontSize: 16,
    lineHeight: 20,
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
    position: 'absolute',
    right: 6,
    bottom: 5,
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.24,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 5,
  },
  inlineSendButtonPressed: {
    opacity: 0.9,
    transform: [{ scale: 0.98 }],
  },
  inlineSendButtonDisabled: {
    opacity: 0.75,
  },
});
