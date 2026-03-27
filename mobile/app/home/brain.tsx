import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  AppState,
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
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { theme } from '@/theme';
import { UiDirectiveCard } from '@/components/ui-directive-card';
import { SlashCommandPalette } from '@/components/SlashCommandPalette';
import { renderAssistantMarkdown } from '@/components/MarkdownRenderer';
import { StreamingAssistantCard } from '@/components/StreamingAssistantCard';
import type {
  EventContactOption,
  EventDraft,
  EventDraftModifications,
  EventPlaceOption,
} from '@/components/event-draft/types';
import { askWithStreaming, waitForRunCompletion } from '@/chat/streaming';
import {
  clearPendingRun,
  loadChatSession,
  loadPendingRun,
  saveChatSession,
  savePendingRun,
  StoredChatSession,
} from '@/chat/session';
import { restoreChatHistory } from '@/chat/threads';
import { routeForLinkedItem, type LinkedItem } from '@/chat/linkedItems';
import type { CommandResult as ThreadCommandResult, EventResolvedStatus } from '@/chat/threads';
import type { UiDirectiveBlock, UiDirectives, UiSubmissionInput } from '@/chat/uiDirectives';
import { LinkedItemsRow } from '@/components/chat/LinkedItemsRow';
import {
  clearEventDraftEditSession,
  consumeEventDraftEditResult,
  createEventDraftEditSession,
} from '@/events/draftEditorSession';
import { normalizeSearch } from '@/utils/text';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
    linked_items?: LinkedItem[];
    event_resolved?: EventResolvedStatus;
    request_error?: RequestErrorMetadata;
    progress_chip?: string;
  };
};

type RequestErrorMetadata = {
  summary: string;
  details: string;
};

type CommandResult = ThreadCommandResult;

type SendMessageInput =
  | string
  | {
      text?: string;
      uiSubmission?: UiSubmissionInput;
    };

type EventAction = {
  type: 'confirm' | 'cancel' | 'edit';
  previewId: string;
};

type EventCommandResultPayload = {
  type?: string;
  preview_id?: string;
  relationship_suggestions?: {
    from_contact_id?: unknown;
    from_display_name?: unknown;
    to_contact_id?: unknown;
    to_display_name?: unknown;
    relationship_type?: unknown;
    reciprocal_type?: unknown;
    confidence?: unknown;
    reasoning?: unknown;
  }[];
  extracted?: {
    title?: unknown;
    summary?: unknown;
    when?: unknown;
    end_when?: unknown;
    where?: unknown;
    tags?: unknown;
    types?: unknown;
  };
  resolution?: {
    contacts?: { contact_id?: unknown; display_name?: unknown }[];
    new_entities?: {
      contacts?: { display_name?: unknown; contact_id?: unknown }[];
      places?: { name?: unknown }[];
      documents?: { reference?: unknown }[];
    };
    matched_place?: {
      place_id?: unknown;
    };
  };
};

const EVENT_CONFIRM_ACTION_ID = 'event_confirmation_action';
const EVENT_CLARIFICATION_ACTION_PREFIX = 'event_clarification_submit';
const EVENT_CLARIFICATION_BLOCK_PREFIX = 'event_clarification:';
const EVENT_CONFIRM_OPTION_PREFIX = 'confirm:';
const EVENT_CANCEL_OPTION_PREFIX = 'cancel:';
const EVENT_EDIT_OPTION_PREFIX = 'edit:';
const EVENT_PREVIEW_BLOCK_PREFIX = 'event_preview:';
const MIN_CHAT_INPUT_HEIGHT = 46;
const MAX_CHAT_INPUT_HEIGHT = 120;
const COMPOSER_KEYBOARD_GAP = 20;

function backendErrorDetails(error: unknown): RequestErrorMetadata {
  const fallbackSummary = 'Request failed';
  const fallbackDetails = 'No extra error details were available.';

  if (error instanceof Error) {
    const err = error as Error & { status?: number };
    const message = err.message?.trim() || fallbackDetails;
    const statusPrefix = typeof err.status === 'number' ? `HTTP ${err.status}: ` : '';
    const details = `${statusPrefix}${message}`.slice(0, 4000);
    return {
      summary: fallbackSummary,
      details,
    };
  }

  if (typeof error === 'string') {
    const details = error.trim() || fallbackDetails;
    return {
      summary: fallbackSummary,
      details: details.slice(0, 4000),
    };
  }

  try {
    const serialized = JSON.stringify(error, null, 2);
    return {
      summary: fallbackSummary,
      details: (serialized || fallbackDetails).slice(0, 4000),
    };
  } catch {
    return {
      summary: fallbackSummary,
      details: fallbackDetails,
    };
  }
}

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

type ConfirmedRelationship = {
  from_contact_id?: string;
  from_display_name?: string;
  to_contact_id?: string;
  to_display_name?: string;
  relationship_type: string;
  reciprocal_type?: string;
  confidence?: string;
  reasoning?: string;
};

function normalizeParticipantName(value: unknown): string {
  return textValue(value).toLowerCase();
}

function buildConfirmedRelationships(
  commandResult: CommandResult | undefined,
  filter?: {
    participantIds?: Set<string>;
    participantNames?: Set<string>;
  },
): ConfirmedRelationship[] {
  if (!commandResult || typeof commandResult !== 'object') return [];
  const payload = commandResult as EventCommandResultPayload;
  const suggestions = Array.isArray(payload.relationship_suggestions)
    ? payload.relationship_suggestions
    : [];

  const confirmedRelationships: ConfirmedRelationship[] = [];
  for (const suggestion of suggestions) {
    const relationshipType = textValue(suggestion.relationship_type);
    if (!relationshipType) {
      continue;
    }

    const fromContactId = textValue(suggestion.from_contact_id);
    const toContactId = textValue(suggestion.to_contact_id);
    const fromDisplayName = textValue(suggestion.from_display_name);
    const toDisplayName = textValue(suggestion.to_display_name);

    if (filter?.participantIds || filter?.participantNames) {
      const fromAllowed = fromContactId
        ? filter.participantIds?.has(fromContactId)
        : filter.participantNames?.has(normalizeParticipantName(fromDisplayName));
      const toAllowed = toContactId
        ? filter.participantIds?.has(toContactId)
        : filter.participantNames?.has(normalizeParticipantName(toDisplayName));
      if (!fromAllowed || !toAllowed) {
        continue;
      }
    }

    confirmedRelationships.push({
      from_contact_id: fromContactId || undefined,
      from_display_name: fromDisplayName || undefined,
      to_contact_id: toContactId || undefined,
      to_display_name: toDisplayName || undefined,
      relationship_type: relationshipType,
      reciprocal_type: textValue(suggestion.reciprocal_type) || undefined,
      confidence: textValue(suggestion.confidence) || undefined,
      reasoning: textValue(suggestion.reasoning) || undefined,
    });
  }

  return confirmedRelationships;
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
    endWhen: textValue(extracted.end_when),
    where: textValue(extracted.where),
    placeId: textValue(payload.resolution?.matched_place?.place_id) || null,
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
    endWhen:
      modifications.end_when === null
        ? ''
        : modifications.end_when === undefined
          ? baseDraft.endWhen
          : modifications.end_when,
    where: modifications.where ?? baseDraft.where,
    placeId:
      modifications.place_id === undefined
        ? baseDraft.placeId
        : textValue(modifications.place_id) || null,
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
  if (normalizedDraftValue(baseDraft.endWhen) !== normalizedDraftValue(nextDraft.endWhen)) {
    modifications.end_when = normalizedDraftValue(nextDraft.endWhen) || null;
  }
  if (normalizedDraftValue(baseDraft.where) !== normalizedDraftValue(nextDraft.where)) {
    modifications.where = normalizedDraftValue(nextDraft.where);
  }
  if ((baseDraft.placeId || null) !== (nextDraft.placeId || null)) {
    modifications.place_id = nextDraft.placeId || null;
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
  if ('end_when' in modifications) labels.push('end');
  if ('where' in modifications) labels.push('where');
  if ('place_id' in modifications) labels.push('place');
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

function formatEventPreviewWhen(value: string): string {
  const raw = value.trim();
  if (!raw) return 'Not specified';
  const normalized = raw.replace('Z', '+00:00');
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    return raw;
  }
  return parsed.toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function updateEventPreviewCard(
  directives: UiDirectives,
  previewId: string,
  draft: EventDraft,
): UiDirectives {
  const participants = draft.participants
    .map((participant) => participant.displayName.trim())
    .filter(Boolean);
  const body = [
    `Title: ${draft.title.trim() || 'Untitled event'}`,
    `Summary: ${draft.summary.trim() || 'No summary provided.'}`,
    `When: ${formatEventPreviewWhen(draft.when)}`,
    `Ends: ${formatEventPreviewWhen(draft.endWhen)}`,
    `Where: ${draft.where.trim() || 'Not specified'}`,
    `Who: ${participants.length > 0 ? participants.join(', ') : 'No participants detected'}`,
    `Tags: ${draft.tags.length > 0 ? draft.tags.join(', ') : 'None'}`,
    `Types: ${draft.types.length > 0 ? draft.types.join(', ') : 'Generic'}`,
  ].join('\n');

  return {
    ...directives,
    blocks: directives.blocks.map((block) =>
      block.id === `${EVENT_PREVIEW_BLOCK_PREFIX}${previewId}`
        ? {
            ...block,
            body,
          }
        : block,
    ),
  };
}

function upsertInfoCardBlock(
  directives: UiDirectives,
  previewId: string,
  blockIdPrefix: string,
  title: string,
  lines: string[],
): UiDirectives {
  const blockId = `${blockIdPrefix}${previewId}`;
  const actionBlockId = `event_actions:${previewId}`;
  const filteredBlocks = directives.blocks.filter((block) => block.id !== blockId);

  if (lines.length === 0) {
    return {
      ...directives,
      blocks: filteredBlocks,
    };
  }

  const cardBlock: UiDirectiveBlock = {
    id: blockId,
    type: 'info_card',
    title,
    body: lines.join('\n'),
  };

  const actionIndex = filteredBlocks.findIndex((block) => block.id === actionBlockId);
  const nextBlocks = [...filteredBlocks];
  if (actionIndex >= 0) {
    nextBlocks.splice(actionIndex, 0, cardBlock);
  } else {
    nextBlocks.push(cardBlock);
  }

  return {
    ...directives,
    blocks: nextBlocks,
  };
}

function updateEventAuxiliaryCards(
  directives: UiDirectives,
  commandResult: CommandResult | undefined,
  previewId: string,
  draft: EventDraft,
): UiDirectives {
  if (!commandResult || typeof commandResult !== 'object') {
    return directives;
  }

  const payload = commandResult as EventCommandResultPayload;
  const selectedNewContacts = draft.participants
    .filter((participant) => participant.contactId.startsWith('new:'))
    .map((participant) => participant.displayName.trim())
    .filter(Boolean);
  const uniqueNewContacts = Array.from(new Set(selectedNewContacts));

  const resolutionNewPlaces = Array.isArray(payload.resolution?.new_entities?.places)
    ? payload.resolution?.new_entities?.places
        .map((place) => textValue(place.name))
        .filter(Boolean)
    : [];
  const extractedWhere = textValue(payload.extracted?.where);
  const normalizedDraftWhere = normalizeSearch(draft.where.trim());
  const normalizedExtractedWhere = normalizeSearch(extractedWhere);
  const whereWasEdited = normalizedDraftWhere !== normalizedExtractedWhere;
  const newPlaces = draft.placeId
    ? []
    : whereWasEdited
      ? resolutionNewPlaces.filter(
          (placeName) =>
            Boolean(normalizedDraftWhere) && normalizeSearch(placeName) === normalizedDraftWhere,
        )
      : resolutionNewPlaces;
  const newDocuments = Array.isArray(payload.resolution?.new_entities?.documents)
    ? payload.resolution?.new_entities?.documents
        .map((document) => textValue(document.reference))
        .filter(Boolean)
    : [];

  const newEntityLines: string[] = [];
  if (uniqueNewContacts.length > 0) {
    newEntityLines.push(`Contacts: ${uniqueNewContacts.join(', ')}`);
  }
  if (newPlaces.length > 0) {
    newEntityLines.push(`Places: ${newPlaces.join(', ')}`);
  }
  if (newDocuments.length > 0) {
    newEntityLines.push(`Documents: ${newDocuments.join(', ')}`);
  }

  const participantIds = new Set(draft.participants.map((participant) => participant.contactId));
  const participantNames = new Set(
    draft.participants
      .map((participant) => normalizeParticipantName(participant.displayName))
      .filter(Boolean),
  );
  const filteredRelationships = buildConfirmedRelationships(commandResult, {
    participantIds,
    participantNames,
  });
  const relationshipLines = filteredRelationships
    .slice(0, 6)
    .map((relationship) => {
      const fromName = textValue(relationship.from_display_name);
      const toName = textValue(relationship.to_display_name);
      const relationshipType = textValue(relationship.relationship_type);
      if (!fromName || !toName || !relationshipType) return '';
      return `${fromName} - ${relationshipType} - ${toName}`;
    })
    .filter(Boolean);

  const withEntities = upsertInfoCardBlock(
    directives,
    previewId,
    'event_new_entities:',
    'New entities',
    newEntityLines,
  );
  return upsertInfoCardBlock(
    withEntities,
    previewId,
    'event_relationships:',
    'Suggested relationships',
    relationshipLines,
  );
}

export default function ChatScreen() {
  const router = useRouter();
  const { token, signOut, email, name, photo, isLoading: isAuthLoading } = useAuth();
  const insets = useSafeAreaInsets();
  const tabBarHeight = useBottomTabBarHeight();
  const scrollY = useRef(new Animated.Value(0)).current;
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
  const [eventEditorPlaces, setEventEditorPlaces] = useState<EventPlaceOption[]>([]);
  const isAtBottomRef = useRef(true);
  const [forceScrollNext, setForceScrollNext] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [expandedErrorMessageIds, setExpandedErrorMessageIds] = useState<Record<string, boolean>>({});
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

  const resumePendingRun = useCallback(async () => {
    if (!token) return;
    const pendingRun = await loadPendingRun();
    if (!pendingRun?.runId) return;

    setForceScrollNext(true);
    setMessages((prev) => {
      if (prev.some((message) => message.id === pendingRun.pendingMessageId)) {
        return prev;
      }
      return [
        ...prev,
        {
          id: pendingRun.pendingMessageId,
          role: 'assistant',
          content: 'Reconnecting...',
          pending: true,
        },
      ];
    });

    const updatePendingMessage = (nextContent: string) => {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingRun.pendingMessageId
            ? {
                ...message,
                content: nextContent,
                pending: true,
                metadata: message.metadata,
              }
            : message,
        ),
      );
    };

    const setProgressChip = (chipLabelRaw: string) => {
      const chipLabel = chipLabelRaw.trim();
      if (!chipLabel) return;

      setMessages((prev) =>
        prev.map((message) => {
          if (message.id !== pendingRun.pendingMessageId) {
            return message;
          }
          if (message.metadata?.progress_chip === chipLabel) {
            return message;
          }
          return {
            ...message,
            metadata: {
              ...message.metadata,
              progress_chip: chipLabel,
            },
          };
        }),
      );
    };

    try {
      await waitForRunCompletion(pendingRun.runId, token, {
        onSessionInfo: (nextThreadId) => {
          setThreadId((prev) => nextThreadId ?? prev);
        },
        onStatus: (statusMessage) => {
          updatePendingMessage(statusMessage || 'Reconnecting...');
        },
        onProgressChip: setProgressChip,
      });

      const restored = await restoreChatHistory(token, {
        threadId: pendingRun.threadId,
        pendingEventId,
      });
      setThreadId(restored.threadId);
      setPendingEventId(restored.pendingEventId);
      if (restored.messages.length > 0) {
        setMessages(restored.messages);
      }
      await clearPendingRun();
    } catch {
      // Keep pending run marker for another retry on next foreground.
    }
  }, [pendingEventId, token]);

  useEffect(() => {
    if (!token || !allowed || isAuthLoading || isBootstrapping) return;
    void resumePendingRun();
  }, [allowed, isAuthLoading, isBootstrapping, resumePendingRun, token]);

  useEffect(() => {
    if (!token || !allowed || isAuthLoading || isBootstrapping) return;

    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState !== 'active') return;

      void (async () => {
        try {
          const restored = await restoreChatHistory(token, {
            threadId,
            pendingEventId,
          });
          setThreadId(restored.threadId);
          setPendingEventId(restored.pendingEventId);
          if (restored.messages.length > 0) {
            setMessages(restored.messages);
          }
          await resumePendingRun();
        } catch {
          // Ignore foreground sync failures and keep current UI state.
        }
      })();
    });

    return () => {
      subscription.remove();
    };
  }, [allowed, isAuthLoading, isBootstrapping, pendingEventId, resumePendingRun, threadId, token]);

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

    const updatePendingMessage = (nextContent: string) => {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? {
                ...message,
                content: nextContent,
                pending: true,
                metadata: message.metadata,
              }
            : message,
        ),
      );
    };

    const setProgressChip = (chipLabelRaw: string) => {
      const chipLabel = chipLabelRaw.trim();
      if (!chipLabel) return;

      setMessages((prev) =>
        prev.map((message) => {
          if (message.id !== pendingId) {
            return message;
          }
          if (message.metadata?.progress_chip === chipLabel) {
            return message;
          }
          return {
            ...message,
            metadata: {
              ...message.metadata,
              progress_chip: chipLabel,
            },
          };
        }),
      );
    };

    setIsSending(true);
    let activeRunId: string | null = null;
    try {
      let streamedContent = '';
      let lastStatus = 'Thinking...';
      const response = await askWithStreaming({
        token,
        question: outboundText,
        pendingEventId,
        uiSubmission,
        callbacks: {
          onSessionInfo: (threadIdFromStream) => {
            setThreadId((prev) => threadIdFromStream ?? prev);
            if (activeRunId) {
              void savePendingRun({
                runId: activeRunId,
                pendingMessageId: pendingId,
                threadId: threadIdFromStream ?? null,
                question: outboundText,
                startedAt: Date.now(),
              });
            }
          },
          onRunId: (runIdFromStream) => {
            activeRunId = runIdFromStream;
            void savePendingRun({
              runId: runIdFromStream,
              pendingMessageId: pendingId,
              threadId: threadId,
              question: outboundText,
              startedAt: Date.now(),
            });
          },
          onStatus: (statusMessage) => {
            lastStatus = statusMessage;
            const isReconnectStatus = statusMessage.toLowerCase().startsWith('reconnecting');
            if (isReconnectStatus) {
              streamedContent = '';
            }
            if (!streamedContent || isReconnectStatus) {
              updatePendingMessage(lastStatus);
            }
          },
          onToken: (delta) => {
            streamedContent += delta;
            updatePendingMessage(streamedContent);
          },
          onClearContent: () => {
            streamedContent = '';
            updatePendingMessage(lastStatus || 'Thinking...');
          },
          onProgressChip: setProgressChip,
        },
      });

      await clearPendingRun();

      setThreadId((prev) => response.thread_id ?? prev);
      const commandResult = response.command_result as CommandResult | undefined;
      const uiDirectives = response.ui_directives;
      const linkedItems = Array.isArray(response.linked_items)
        ? (response.linked_items as LinkedItem[])
        : [];
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
                  commandResult || uiDirectives || linkedItems.length > 0
                    ? {
                        command_result: commandResult,
                        ui_directives: uiDirectives,
                        linked_items: linkedItems.length > 0 ? linkedItems : undefined,
                      }
                    : undefined,
              }
            : message,
        ),
      );
    } catch (error) {
      const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
      if (authExpired) {
        await clearPendingRun();
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
      const requestError = backendErrorDetails(error);
      if (!activeRunId) {
        await clearPendingRun();
      }
      setForceScrollNext(true);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingId
            ? {
                ...message,
                content: activeRunId
                  ? 'Reconnecting...'
                  : 'I hit a snag reaching the brain. Try again in a moment.',
                pending: Boolean(activeRunId),
                metadata: activeRunId
                  ? message.metadata
                  : {
                      ...message.metadata,
                      request_error: requestError,
                    },
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

  const loadEventEditorPlaces = useCallback(async (): Promise<EventPlaceOption[]> => {
    if (!token) return [];
    if (eventEditorPlaces.length > 0) return eventEditorPlaces;

    try {
      const response = (await apiFetch('/mobile/places?limit=500', { token })) as {
        places?: EventPlaceOption[];
      };
      const places = Array.isArray(response.places) ? response.places : [];
      setEventEditorPlaces(places);
      return places;
    } catch {
      return [];
    }
  }, [eventEditorPlaces, token]);

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
          const loadedPlaces = await loadEventEditorPlaces();
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
            availablePlaces: loadedPlaces,
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
          const baseDraftForConfirm = buildEventDraft(commandResult, action.previewId);
          const participantIds = modifications.contact_ids
            ? new Set(modifications.contact_ids)
            : baseDraftForConfirm
              ? new Set(baseDraftForConfirm.participants.map((participant) => participant.contactId))
              : undefined;
          const participantNames = baseDraftForConfirm
            ? new Set(
                baseDraftForConfirm.participants
                  .filter((participant) =>
                    participantIds ? participantIds.has(participant.contactId) : true,
                  )
                  .map((participant) => normalizeParticipantName(participant.displayName))
                  .filter(Boolean),
              )
            : undefined;
          const confirmedRelationships = buildConfirmedRelationships(commandResult, {
            participantIds,
            participantNames,
          });
          const confirmPayload = {
            preview_id: action.previewId,
            confirmed: true,
            modifications: {
              ...modifications,
              confirmed_relationships: confirmedRelationships,
            },
            skip_entities: {},
          };
          await apiFetch('/mobile/commands/event/confirm', {
            method: 'POST',
            body: JSON.stringify(
              action.type === 'confirm'
                ? confirmPayload
                : {
                    preview_id: action.previewId,
                    confirmed: false,
                  },
            ),
            token,
          });

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

      const submissionBlockId = submission.block_id?.trim() || '';
      const isEventClarificationSubmission =
        submission.action_id?.startsWith(EVENT_CLARIFICATION_ACTION_PREFIX) &&
        submissionBlockId.startsWith(EVENT_CLARIFICATION_BLOCK_PREFIX);

      if (isEventClarificationSubmission) {
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
      loadEventEditorPlaces,
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

  const handleLinkedItemPress = useCallback(
    (item: LinkedItem) => {
      const route = routeForLinkedItem(item);
      if (!route) return;
      router.push(route);
    },
    [router],
  );

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
    lastMessage?.metadata?.progress_chip,
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
          onContentSizeChange={() => {
            if (!isAtBottomRef.current && !forceScrollNext) return;
            requestAnimationFrame(() => {
              listRef.current?.scrollToEnd({ animated: forceScrollNext });
            });
          }}
          ListFooterComponent={<View style={{ height: listBottomInset }} />}
          contentContainerStyle={[
            styles.listContent,
            {
              paddingTop: insets.top + COLLAPSING_TOP_BAR_HEIGHT + COLLAPSING_CONTENT_TOP_PADDING,
            },
          ]}
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
            useNativeDriver: false,
            listener: (event: any) => {
              const { contentOffset, layoutMeasurement, contentSize } = event.nativeEvent;
              const distanceFromBottom =
                contentSize.height - (contentOffset.y + layoutMeasurement.height);
              isAtBottomRef.current = distanceFromBottom < 48;
            },
          })}
          scrollEventThrottle={16}
          renderItem={({ item }) => {
            if (item.role === 'assistant' && item.pending) {
              return (
                <StreamingAssistantCard
                  content={item.content}
                  progressChip={item.metadata?.progress_chip}
                />
              );
            }

            const commandResult = item.metadata?.command_result;
            const previewId = extractEventPreviewId(commandResult);
            const isSupersededEventCard = Boolean(
              previewId && pendingEventId && previewId !== pendingEventId,
            );
            const previewModifications = previewId
              ? eventDraftModificationsByPreview[previewId]
              : undefined;
            const directives = item.metadata?.ui_directives;
            const linkedItems = item.metadata?.linked_items || [];
            let directivesForCard = directives;
            if (previewId && directives && previewModifications) {
              const baseDraft = buildEventDraft(commandResult, previewId);
              if (baseDraft) {
                const contactNameById = new Map(
                  eventEditorContacts.map((contact) => [contact.contact_id, contact.display_name]),
                );
                for (const participant of baseDraft.participants) {
                  if (!contactNameById.has(participant.contactId)) {
                    contactNameById.set(participant.contactId, participant.displayName);
                  }
                }
                const modifiedDraft = applyDraftModifications(
                  baseDraft,
                  previewModifications,
                  contactNameById,
                );
                const withUpdatedPreview = updateEventPreviewCard(
                  directives,
                  previewId,
                  modifiedDraft,
                );
                directivesForCard = updateEventAuxiliaryCards(
                  withUpdatedPreview,
                  commandResult,
                  previewId,
                  modifiedDraft,
                );
              }
            }
            const requestError = item.metadata?.request_error;
            const isErrorExpanded = Boolean(expandedErrorMessageIds[item.id]);

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
                {directivesForCard && (
                  <View style={styles.commandCardWrap}>
                    <UiDirectiveCard
                      directives={directivesForCard}
                      isSubmitting={isSending || isConfirmingEvent || isSupersededEventCard}
                      resolvedStatus={item.metadata?.event_resolved}
                      onSubmit={(submission) => {
                        void handleDirectiveSubmission(
                          item.id,
                          directivesForCard,
                          submission,
                          commandResult,
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
                {linkedItems.length > 0 ? (
                  <LinkedItemsRow
                    items={linkedItems}
                    disabled={item.pending || isSending}
                    onPressItem={handleLinkedItemPress}
                  />
                ) : null}
                {requestError ? (
                  <View style={styles.errorCardWrap}>
                    <Pressable
                      onPress={() => {
                        setExpandedErrorMessageIds((prev) => ({
                          ...prev,
                          [item.id]: !prev[item.id],
                        }));
                      }}
                      style={({ pressed }) => [
                        styles.errorToggle,
                        pressed && styles.errorTogglePressed,
                      ]}
                    >
                      <Text style={styles.errorToggleText}>
                        {isErrorExpanded ? 'Hide backend error' : 'Show backend error'}
                      </Text>
                    </Pressable>
                    {isErrorExpanded ? (
                      <View style={styles.errorDetailsWrap}>
                        <Text style={styles.errorSummary}>{requestError.summary}</Text>
                        <Text style={styles.errorDetails} selectable>
                          {requestError.details}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                ) : null}
              </View>
            );
          }}
        />
        <CollapsingTopBar
          title="Brain"
          secondaryTitle={'Talk to "your" memory'}
          scrollY={scrollY}
          profileName={name || email || 'You'}
          profilePhoto={photo}
          token={token}
          onPressProfile={() => router.push('/settings')}
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
    paddingTop: COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT + 20,
  },
  list: {
    flex: 1,
  },
  listContent: {
    paddingHorizontal: 20,
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
  errorCardWrap: {
    marginTop: 10,
    gap: 8,
  },
  errorToggle: {
    alignSelf: 'flex-start',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: '#FDECEC',
    borderWidth: 1,
    borderColor: '#F3B4B4',
  },
  errorTogglePressed: {
    opacity: 0.85,
  },
  errorToggleText: {
    fontSize: 12,
    color: '#8A1F1F',
    fontWeight: '600',
  },
  errorDetailsWrap: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#F3B4B4',
    backgroundColor: '#FFF6F6',
    padding: 10,
    gap: 6,
  },
  errorSummary: {
    fontSize: 12,
    lineHeight: 16,
    fontWeight: '700',
    color: '#8A1F1F',
  },
  errorDetails: {
    fontSize: 12,
    lineHeight: 17,
    color: '#8A1F1F',
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
