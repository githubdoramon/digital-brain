import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  AppState,
  FlatList,
  Image,
  Keyboard,
  KeyboardEvent,
  KeyboardAvoidingView,
  Linking,
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
import { GeneratedFilesRow } from '@/components/chat/GeneratedFilesRow';
import { EventMediaSuggestionCard } from '@/components/event-draft/EventMediaSuggestionCard';
import { appendEventPhotoDebugLog } from '@/debug/eventPhotoDebugLog';
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
import { ChatComposer } from '@/components/chat/ChatComposer';
import type {
  EventContactOption,
  EventDraft,
  EventDraftModifications,
  EventDraftOperation,
  EventMatchCandidate,
  EventPhoto,
  EventPlaceOption,
} from '@/components/event-draft/types';
import { askWithStreaming, waitForRunCompletion } from '@/chat/streaming';
import {
  buildComposerMediaAttachment,
  MAX_CHAT_MEDIA_ATTACHMENTS,
  type ComposerMediaAttachment,
  toChatMediaAttachmentPayload,
} from '@/chat/mediaAttachments';
import {
  clearPendingRun,
  loadChatSession,
  loadPendingRun,
  saveChatSession,
  savePendingRun,
  StoredChatSession,
} from '@/chat/session';
import { downloadGeneratedFile } from '@/chat/generatedFileDownloads';
import { loadThreadHistory, restoreChatHistory } from '@/chat/threads';
import type { GeneratedFile } from '@/chat/generatedFiles';
import { routeForLinkedItem, type LinkedItem } from '@/chat/linkedItems';
import type {
  CommandResolvedMeta,
  CommandResult as ThreadCommandResult,
  MessageMediaAttachment,
} from '@/chat/threads';
import type { UiDirectiveBlock, UiDirectives, UiSubmissionInput } from '@/chat/uiDirectives';
import {
  applyContactDraftModifications,
  buildContactDraft,
  buildContactDraftModifications,
  contactDraftModificationSummary,
  extractContactPreviewId,
} from '@/contact-draft/proposal';
import {
  consumeContactDraftEditResult,
  createContactDraftEditSession,
} from '@/contact-draft/draftEditorSession';
import type { ContactDraftModifications } from '@/contact-draft/types';
import { LinkedItemsRow } from '@/components/chat/LinkedItemsRow';
import { formatDraftDateTime, formatInstantDateTime } from '@/components/event-draft/dateTime';
import {
  clearEventDraftEditSession,
  consumeEventDraftEditResult,
  createEventDraftEditSession,
} from '@/events/draftEditorSession';
import { useAppNotice } from '@/hooks/useAppNotice';
import { useSingleImagePicker } from '@/hooks/useImagePicker';
import { normalizeSearch } from '@/utils/text';

import { getHomeTabBarClearance } from './tabBarMetrics';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
    linked_items?: LinkedItem[];
    generated_files?: GeneratedFile[];
    command_resolved?: CommandResolvedMeta;
    media_attachments?: MessageMediaAttachment[];
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
      pendingCommandId?: string | null;
      uiSubmission?: UiSubmissionInput;
      mediaAttachments?: ComposerMediaAttachment[];
    };

type EventAction = {
  type: 'confirm' | 'cancel' | 'edit';
  previewId: string;
};

type ContactAction = {
  type: 'confirm' | 'cancel' | 'edit';
  previewId: string;
};

type ChatConversationScreenProps = {
  mode?: 'main' | 'thread';
  initialThreadId?: string | null;
  tabBarHeight?: number;
};

type EventMatchCandidatePayload = {
  event_id?: unknown;
  title?: unknown;
  summary?: unknown;
  start_date?: unknown;
  end_date?: unknown;
  place?: {
    place_id?: unknown;
    name?: unknown;
    city?: unknown;
    country?: unknown;
  } | null;
  match_score?: unknown;
  match_sources?: unknown;
};

type EventCommandResultPayload = {
  type?: string;
  preview_id?: string;
  operation?: string;
  existing_event_id?: string | null;
  matched_event?: EventMatchCandidatePayload | null;
  candidate_events?: EventMatchCandidatePayload[];
  media_suggestions?: EventMediaSuggestionPayload[];
  original_extracted?: {
    title?: unknown;
    summary?: unknown;
    when?: unknown;
    end_when?: unknown;
    where?: unknown;
    tags?: unknown;
    types?: unknown;
  };
  original_resolution?: {
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

type EventMediaSuggestionPayload = {
  asset_id?: unknown;
  media_type?: unknown;
  checksum?: unknown;
  file_name?: unknown;
  mime_type?: unknown;
  captured_at?: unknown;
  width?: unknown;
  height?: unknown;
  duration_seconds?: unknown;
  distance_m?: unknown;
  temporal_distance_seconds?: unknown;
  has_gps?: unknown;
  status?: unknown;
  match_reasons?: unknown;
  thumbnail_path?: unknown;
};

const EVENT_CONFIRM_ACTION_ID = 'event_confirmation_action';
const EVENT_CLARIFICATION_ACTION_PREFIX = 'event_clarification_submit';
const EVENT_CLARIFICATION_BLOCK_PREFIX = 'event_clarification:';
const EVENT_CONFIRM_OPTION_PREFIX = 'confirm:';
const EVENT_CANCEL_OPTION_PREFIX = 'cancel:';
const EVENT_EDIT_OPTION_PREFIX = 'edit:';
const EVENT_PREVIEW_BLOCK_PREFIX = 'event_preview:';
const CONTACT_CONFIRM_ACTION_ID = 'contact_confirmation_action';
const CONTACT_CLARIFICATION_ACTION_PREFIX = 'contact_clarification_submit';
const CONTACT_EDIT_ACTION_PREFIX = 'contact_edit_submit';
const CONTACT_CONFIRM_OPTION_PREFIX = 'confirm:';
const CONTACT_CANCEL_OPTION_PREFIX = 'cancel:';
const CONTACT_EDIT_OPTION_PREFIX = 'edit:';
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

function conciseBackendErrorMessage(error: unknown, fallback: string): string {
  const detail = backendErrorDetails(error)
    .details.replace(/^HTTP \d+:\s*/i, '')
    .replace(/^\{"detail":\s*/i, '')
    .replace(/"\}\s*$/i, '')
    .trim();

  if (!detail) return fallback;
  return detail.length > 220 ? `${detail.slice(0, 217).trimEnd()}...` : detail;
}

function shouldKeepPendingRun(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const typedError = error as Error & { errorCode?: string; isReconnectable?: boolean };
  if (typedError.errorCode === 'stream_backgrounded') {
    return true;
  }
  if (typedError.isReconnectable === true) {
    return true;
  }
  if (typedError.isReconnectable === false) {
    return false;
  }
  const message = typedError.message.toLowerCase();
  return (
    message.includes('connection abort') ||
    message.includes('network request failed') ||
    message.includes('stream ended before final response bundle') ||
    message.includes('timed out waiting for run to complete') ||
    message.includes('terminated')
  );
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

function optionLabelForField(
  field: ReturnType<typeof fieldForSubmission>,
  rawValue: string,
): string {
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

function parseContactAction(optionIdRaw: unknown): ContactAction | null {
  if (typeof optionIdRaw !== 'string') return null;
  const optionId = optionIdRaw.trim();
  if (optionId.startsWith(CONTACT_CONFIRM_OPTION_PREFIX)) {
    const previewId = optionId.slice(CONTACT_CONFIRM_OPTION_PREFIX.length).trim();
    if (previewId) {
      return { type: 'confirm', previewId };
    }
  }
  if (optionId.startsWith(CONTACT_EDIT_OPTION_PREFIX)) {
    const previewId = optionId.slice(CONTACT_EDIT_OPTION_PREFIX.length).trim();
    if (previewId) {
      return { type: 'edit', previewId };
    }
  }
  if (optionId.startsWith(CONTACT_CANCEL_OPTION_PREFIX)) {
    const previewId = optionId.slice(CONTACT_CANCEL_OPTION_PREFIX.length).trim();
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
  return value.map((entry) => textValue(entry)).filter(Boolean);
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

function numberValue(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function nullableText(value: unknown): string | null {
  const text = textValue(value);
  return text || null;
}

function buildEventMatchCandidate(
  candidate: EventMatchCandidatePayload | null | undefined,
): EventMatchCandidate | null {
  if (!candidate || typeof candidate !== 'object') return null;
  const eventId = textValue(candidate.event_id);
  if (!eventId) return null;
  const placeRaw = candidate.place;
  const place =
    placeRaw && typeof placeRaw === 'object'
      ? {
          placeId: textValue(placeRaw.place_id),
          name: textValue(placeRaw.name),
          city: nullableText(placeRaw.city),
          country: nullableText(placeRaw.country),
        }
      : null;
  return {
    eventId,
    title: textValue(candidate.title),
    summary: textValue(candidate.summary),
    startDate: nullableText(candidate.start_date),
    endDate: nullableText(candidate.end_date),
    place: place && place.placeId ? place : null,
    matchScore: numberValue(candidate.match_score),
    matchSources: stringArrayValue(candidate.match_sources),
  };
}

function buildEventCandidateList(
  candidates: EventMatchCandidatePayload[] | undefined,
): EventMatchCandidate[] {
  if (!Array.isArray(candidates)) return [];
  const out: EventMatchCandidate[] = [];
  const seen = new Set<string>();
  for (const entry of candidates) {
    const built = buildEventMatchCandidate(entry);
    if (!built) continue;
    if (seen.has(built.eventId)) continue;
    seen.add(built.eventId);
    out.push(built);
  }
  return out;
}

function buildEventMediaSuggestions(
  suggestions: EventMediaSuggestionPayload[] | undefined,
): EventPhoto[] {
  if (!Array.isArray(suggestions)) return [];
  const seen = new Set<string>();
  return suggestions.flatMap((suggestion) => {
    const assetId = textValue(suggestion.asset_id);
    if (!assetId || seen.has(assetId) || suggestion.status === 'removed') return [];
    seen.add(assetId);
    return [
      {
        asset_id: assetId,
        media_type: nullableText(suggestion.media_type),
        checksum: nullableText(suggestion.checksum),
        file_name: nullableText(suggestion.file_name),
        mime_type: nullableText(suggestion.mime_type),
        captured_at: nullableText(suggestion.captured_at),
        width: suggestion.width == null ? null : numberValue(suggestion.width),
        height: suggestion.height == null ? null : numberValue(suggestion.height),
        duration_seconds:
          suggestion.duration_seconds == null ? null : numberValue(suggestion.duration_seconds),
        distance_m: suggestion.distance_m == null ? null : numberValue(suggestion.distance_m),
        temporal_distance_seconds:
          suggestion.temporal_distance_seconds == null
            ? null
            : numberValue(suggestion.temporal_distance_seconds),
        has_gps: suggestion.has_gps === true,
        status: 'included',
        match_reasons: stringArrayValue(suggestion.match_reasons),
        thumbnail_path: nullableText(suggestion.thumbnail_path),
      } satisfies EventPhoto,
    ];
  });
}

function buildEventDraftFromPayload(
  payload: EventCommandResultPayload,
  previewId: string,
  options?: {
    useOriginalValues?: boolean;
    forceOperation?: EventDraftOperation;
  },
): EventDraft | null {
  const payloadPreviewId = textValue(payload.preview_id);
  if (payloadPreviewId !== previewId) return null;

  const useOriginalValues = options?.useOriginalValues === true;
  const extracted = useOriginalValues ? payload.original_extracted : payload.extracted;
  const resolution = useOriginalValues ? payload.original_resolution : payload.resolution;
  if (!extracted || typeof extracted !== 'object') return null;

  const resolvedContacts = Array.isArray(resolution?.contacts) ? resolution.contacts : [];
  const newEntityContacts = Array.isArray(resolution?.new_entities?.contacts)
    ? resolution.new_entities?.contacts
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

  const operation: EventDraftOperation =
    options?.forceOperation ?? (payload.operation === 'update' ? 'update' : 'create');
  const existingEventId = textValue(payload.existing_event_id) || null;
  const matchedEvent =
    operation === 'update' ? buildEventMatchCandidate(payload.matched_event) : null;

  return {
    title: textValue(extracted.title),
    summary: textValue(extracted.summary),
    when: textValue(extracted.when),
    endWhen: textValue(extracted.end_when),
    where: textValue(extracted.where),
    placeId: textValue(resolution?.matched_place?.place_id) || null,
    tags: stringArrayValue(extracted.tags),
    types: stringArrayValue(extracted.types),
    participants,
    operation: operation === 'update' && existingEventId ? 'update' : 'create',
    existingEventId: operation === 'update' ? existingEventId : null,
    matchedEvent,
    mediaSuggestions: buildEventMediaSuggestions(payload.media_suggestions),
  };
}

function buildEventDraft(
  commandResult: CommandResult | undefined,
  previewId: string,
): EventDraft | null {
  if (!commandResult || typeof commandResult !== 'object') return null;
  return buildEventDraftFromPayload(commandResult as EventCommandResultPayload, previewId);
}

function buildCreateFallbackEventDraft(
  commandResult: CommandResult | undefined,
  previewId: string,
): EventDraft | null {
  if (!commandResult || typeof commandResult !== 'object') return null;
  return buildEventDraftFromPayload(commandResult as EventCommandResultPayload, previewId, {
    useOriginalValues: true,
    forceOperation: 'create',
  });
}

function applyDraftModifications(
  baseDraft: EventDraft,
  modifications: EventDraftModifications | undefined,
  contactNameById: Map<string, string>,
  candidateEvents?: EventMatchCandidate[],
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
  const mediaSuggestions =
    modifications.media_asset_ids === undefined
      ? baseDraft.mediaSuggestions
      : baseDraft.mediaSuggestions.filter((suggestion) =>
          modifications.media_asset_ids?.includes(suggestion.asset_id),
        );

  const operation: EventDraftOperation = modifications.operation ?? baseDraft.operation;
  const existingEventId =
    modifications.existing_event_id === undefined
      ? baseDraft.existingEventId
      : modifications.existing_event_id;
  let matchedEvent = baseDraft.matchedEvent;
  if (operation === 'create') {
    matchedEvent = null;
  } else if (existingEventId && candidateEvents && candidateEvents.length > 0) {
    matchedEvent =
      candidateEvents.find((candidate) => candidate.eventId === existingEventId) ??
      baseDraft.matchedEvent;
  }

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
    operation: operation === 'update' && existingEventId ? 'update' : 'create',
    existingEventId: operation === 'update' ? existingEventId : null,
    matchedEvent: operation === 'update' ? matchedEvent : null,
    mediaSuggestions,
  };
}

function sameStringList(left: string[], right: string[]) {
  if (left.length !== right.length) return false;
  return left.every((entry, index) => entry === right[index]);
}

function buildDraftModifications(
  baseDraft: EventDraft,
  nextDraft: EventDraft,
): EventDraftModifications {
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
  const baseMediaIds = baseDraft.mediaSuggestions.map((suggestion) => suggestion.asset_id);
  const nextMediaIds = nextDraft.mediaSuggestions.map((suggestion) => suggestion.asset_id);
  if (!sameStringList(baseMediaIds, nextMediaIds)) {
    modifications.media_asset_ids = nextMediaIds;
  }

  if (baseDraft.operation !== nextDraft.operation) {
    modifications.operation = nextDraft.operation;
  }
  if ((baseDraft.existingEventId || null) !== (nextDraft.existingEventId || null)) {
    modifications.existing_event_id = nextDraft.existingEventId ?? null;
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
  if ('media_asset_ids' in modifications) labels.push('media');
  return labels.join(', ');
}

function clarificationIdFromAction(
  actionIdRaw: string | undefined,
  actionPrefix: string,
): string | null {
  if (!actionIdRaw) return null;
  const actionId = actionIdRaw.trim();
  const prefix = `${actionPrefix}:`;
  if (!actionId.startsWith(prefix)) {
    return null;
  }
  const clarificationId = actionId.slice(prefix.length).trim();
  return clarificationId || null;
}

function extractClarificationDirectiveId(directives: UiDirectives | undefined): string | null {
  if (!directives) return null;

  for (const block of directives.blocks || []) {
    if (block.type !== 'clarification_form') continue;
    const actionId = block.action_id?.trim();
    const eventClarificationId = clarificationIdFromAction(
      actionId,
      EVENT_CLARIFICATION_ACTION_PREFIX,
    );
    if (eventClarificationId) {
      return eventClarificationId;
    }

    const contactClarificationId = clarificationIdFromAction(
      actionId,
      CONTACT_CLARIFICATION_ACTION_PREFIX,
    );
    if (contactClarificationId) {
      return contactClarificationId;
    }
  }

  return null;
}

function isClarificationDirective(directives: UiDirectives | undefined): boolean {
  if (!directives) return false;
  return (directives.blocks || []).some((block) => block.type === 'clarification_form');
}

function formatEventPreviewWhen(value: string): string {
  return formatInstantDateTime(value);
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
    `When: ${formatDraftDateTime(draft.when)}`,
    `Ends: ${formatDraftDateTime(draft.endWhen)}`,
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
            title: draft.operation === 'update' ? 'Event update preview' : 'Event preview',
            description:
              draft.operation === 'update'
                ? 'Review this before updating the event.'
                : 'Review this before creating the event.',
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

function updateEventMatchCards(
  directives: UiDirectives,
  previewId: string,
  draft: EventDraft,
  candidateEvents: EventMatchCandidate[],
): UiDirectives {
  const filteredBlocks = directives.blocks.filter(
    (block) =>
      block.id !== `event_matched:${previewId}` && block.id !== `event_candidates:${previewId}`,
  );
  const nextDirectives = {
    ...directives,
    blocks: filteredBlocks,
  };

  if (draft.operation === 'update' && draft.matchedEvent) {
    const matchLines = [
      `Title: ${draft.matchedEvent.title || 'Untitled event'}`,
      `When: ${formatEventPreviewWhen(draft.matchedEvent.startDate || '')}`,
    ];
    if (draft.matchedEvent.place?.name) {
      matchLines.push(`Where: ${draft.matchedEvent.place.name}`);
    }
    if (draft.matchedEvent.matchScore > 0) {
      matchLines.push(`Match confidence: ${Math.round(draft.matchedEvent.matchScore)}%`);
    }
    return upsertInfoCardBlock(
      nextDirectives,
      previewId,
      'event_matched:',
      'Matches existing event',
      matchLines,
    );
  }

  if (draft.operation === 'create' && candidateEvents.length > 0) {
    return upsertInfoCardBlock(
      nextDirectives,
      previewId,
      'event_candidates:',
      'Similar events',
      candidateEvents
        .slice(0, 3)
        .map(
          (candidate) =>
            `${candidate.title || 'Untitled event'} - ${formatEventPreviewWhen(candidate.startDate || '')}`,
        ),
    );
  }

  return nextDirectives;
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
    ? payload.resolution?.new_entities?.places.map((place) => textValue(place.name)).filter(Boolean)
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

function updateContactDraftCards(
  directives: UiDirectives,
  previewId: string,
  draft: ReturnType<typeof buildContactDraft>,
): UiDirectives {
  if (!draft) return directives;
  const placeNameByReference = new Map(
    draft.places.map((place) => [place.reference, place.name.trim()]),
  );
  const contactNameByReference = new Map(
    draft.contacts.map((contact) => [contact.reference, contact.displayName.trim()]),
  );

  const explicitLines: string[] = [];
  for (const contact of draft.contacts) {
    explicitLines.push(
      `${contact.operation === 'create' ? 'Create' : 'Update'} contact: ${contact.displayName.trim() || contact.reference}`,
    );
  }
  for (const relationship of draft.relationships.filter((item) => item.kind === 'explicit')) {
    explicitLines.push(
      `Relationship: ${relationship.fromDisplayName || relationship.fromReference} -> ${relationship.relationshipType || 'related'} -> ${relationship.toDisplayName || relationship.toReference}`,
    );
  }
  for (const place of draft.places) {
    explicitLines.push(`Place: ${place.name.trim() || place.reference}`);
  }
  for (const link of draft.placeLinks) {
    const contactName =
      contactNameByReference.get(link.contactReference) ||
      link.contactDisplayName ||
      link.contactReference;
    const placeName =
      placeNameByReference.get(link.placeReference) || link.placeName || link.placeReference;
    explicitLines.push(`Link: ${contactName} -> ${placeName} as ${link.role || 'related'}`);
  }

  const derivedLines = draft.relationships
    .filter((item) => item.kind === 'derived' && item.enabled)
    .map(
      (relationship) =>
        `Infer: ${relationship.fromDisplayName || relationship.fromReference} -> ${relationship.relationshipType || 'related'} -> ${relationship.toDisplayName || relationship.toReference}`,
    );

  const updatedBlocks = pruneContactPreviewBlocks(directives, previewId).blocks.map((block) => {
    if (block.id === `contact_preview:${previewId}`) {
      return {
        ...block,
        body: [...explicitLines, ...derivedLines].join('\n'),
      };
    }
    return block;
  });

  return { ...directives, blocks: updatedBlocks };
}

function pruneContactPreviewBlocks(directives: UiDirectives, previewId: string): UiDirectives {
  const hiddenIds = new Set([
    `contact_explicit:${previewId}`,
    `contact_derived:${previewId}`,
    `contact_edit:${previewId}`,
    `contact_edit_hint:${previewId}`,
  ]);
  return {
    ...directives,
    blocks: directives.blocks.filter((block) => !hiddenIds.has(block.id)),
  };
}

export function ChatConversationScreen({
  mode = 'main',
  initialThreadId = null,
  tabBarHeight = 0,
}: ChatConversationScreenProps) {
  const router = useRouter();
  const { token, refreshToken, signOut, email, name, photo, isLoading: isAuthLoading } = useAuth();
  const { showError, showSuccess } = useAppNotice();
  const { pickImages, imagePickerSheet } = useSingleImagePicker();
  const insets = useSafeAreaInsets();
  const isMainChat = mode === 'main';
  const tabBarClearance = isMainChat
    ? Math.max(tabBarHeight, getHomeTabBarClearance(insets.bottom))
    : insets.bottom;
  const scrollY = useRef(new Animated.Value(0)).current;
  const listRef = useRef<FlatList<Message>>(null);
  const inputRef = useRef<TextInput>(null);
  const scrollFallbackTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [input, setInput] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(initialThreadId);
  const [isConfirmingEvent, setIsConfirmingEvent] = useState(false);
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  const [eventDraftModificationsByPreview, setEventDraftModificationsByPreview] = useState<
    Record<string, EventDraftModifications>
  >({});
  const [activeDraftEditorSessionId, setActiveDraftEditorSessionId] = useState<string | null>(null);
  const [contactDraftModificationsByPreview, setContactDraftModificationsByPreview] = useState<
    Record<string, ContactDraftModifications>
  >({});
  const [activeContactDraftEditorSessionId, setActiveContactDraftEditorSessionId] = useState<
    string | null
  >(null);
  const [eventEditorContacts, setEventEditorContacts] = useState<EventContactOption[]>([]);
  const [eventEditorPlaces, setEventEditorPlaces] = useState<EventPlaceOption[]>([]);
  const isAtBottomRef = useRef(true);
  const [forceScrollNext, setForceScrollNext] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [expandedErrorMessageIds, setExpandedErrorMessageIds] = useState<Record<string, boolean>>(
    {},
  );
  const [composerMediaAttachments, setComposerMediaAttachments] = useState<
    ComposerMediaAttachment[]
  >([]);
  const hasHydratedSessionRef = useRef(false);
  const restoreGenerationRef = useRef(0);
  const [composerHeight, setComposerHeight] = useState(0);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const commandsEnabled = isMainChat;
  const composerBottomOffset = keyboardVisible
    ? Platform.OS === 'ios'
      ? Math.max(0, keyboardHeight - insets.bottom) + COMPOSER_KEYBOARD_GAP
      : Math.max(0, keyboardHeight - insets.bottom) + 2 * COMPOSER_KEYBOARD_GAP
    : 0;
  const listBottomInset =
    composerHeight > 0
      ? composerHeight + composerBottomOffset + 16
      : tabBarClearance + keyboardHeight + 80;

  const allowedEmails = (process.env.EXPO_PUBLIC_ALLOWED_EMAILS ?? '')
    .split(',')
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  const allowed =
    allowedEmails.length === 0 || (email ? allowedEmails.includes(email.toLowerCase()) : false);
  const trimmedInputForSend = input.trim();
  const isBlockedCommandInput = !commandsEnabled && trimmedInputForSend.startsWith('/');
  const canSend =
    (trimmedInputForSend.length > 0 || composerMediaAttachments.length > 0) &&
    !isSending &&
    allowed &&
    !isBlockedCommandInput;

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
    hasHydratedSessionRef.current = false;
    if (!isMainChat) {
      setThreadId(initialThreadId);
      setPendingEventId(null);
    }
  }, [initialThreadId, isMainChat]);

  useEffect(() => {
    let cancelled = false;
    const restoreGeneration = restoreGenerationRef.current + 1;
    restoreGenerationRef.current = restoreGeneration;

    const isCurrentRestore = () => !cancelled && restoreGenerationRef.current === restoreGeneration;

    const restoreSession = async () => {
      if (isAuthLoading) {
        return;
      }

      if (!token || !allowed) {
        hasHydratedSessionRef.current = false;
        if (!isCurrentRestore()) return;
        setThreadId(initialThreadId);
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
        const restored = isMainChat
          ? await restoreChatHistory(token, await loadChatSession())
          : initialThreadId
            ? await loadThreadHistory(token, initialThreadId)
            : { threadId: null, pendingEventId: null, messages: [] };
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
  }, [allowed, initialThreadId, isAuthLoading, isMainChat, signOut, starterMessages, token]);

  useEffect(() => {
    if (!isMainChat) return;
    if (isBootstrapping || isAuthLoading) return;
    const stored: StoredChatSession = {
      threadId,
      pendingEventId,
    };
    void saveChatSession(stored);
  }, [threadId, pendingEventId, isBootstrapping, isAuthLoading, isMainChat]);

  const resumePendingRun = useCallback(async () => {
    if (!token) return;
    const pendingRun = await loadPendingRun();
    if (!pendingRun?.runId) return;
    if (!isMainChat) {
      const activeThreadId = initialThreadId ?? threadId;
      if (!activeThreadId || pendingRun.threadId !== activeThreadId) {
        return;
      }
    }

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

      const restored = isMainChat
        ? await restoreChatHistory(token, {
            threadId: pendingRun.threadId,
            pendingEventId,
          })
        : pendingRun.threadId
          ? await loadThreadHistory(token, pendingRun.threadId)
          : { threadId: null, pendingEventId: null, messages: [] };
      setThreadId(restored.threadId);
      setPendingEventId(restored.pendingEventId);
      if (restored.messages.length > 0) {
        setMessages(restored.messages);
      }
      await clearPendingRun();
    } catch (error) {
      if (shouldKeepPendingRun(error)) {
        return;
      }
      updatePendingMessage(conciseBackendErrorMessage(error, 'I hit a snag reaching the brain.'));
      setMessages((prev) =>
        prev.map((message) =>
          message.id === pendingRun.pendingMessageId
            ? {
                ...message,
                pending: false,
                metadata: {
                  ...message.metadata,
                  request_error: backendErrorDetails(error),
                },
              }
            : message,
        ),
      );
      await clearPendingRun();
    }
  }, [initialThreadId, isMainChat, pendingEventId, threadId, token]);

  useEffect(() => {
    if (!isMainChat) return;
    if (!token || !allowed || isAuthLoading || isBootstrapping) return;
    void resumePendingRun();
  }, [allowed, isAuthLoading, isBootstrapping, isMainChat, resumePendingRun, token]);

  const syncLatestThreadState = useCallback(async () => {
    if (!token || !allowed || isAuthLoading || isBootstrapping) return;

    const restored = isMainChat
      ? await restoreChatHistory(token, {
          threadId,
          pendingEventId,
        })
      : threadId
        ? await loadThreadHistory(token, threadId)
        : { threadId: null, pendingEventId: null, messages: [] };
    setThreadId(restored.threadId);
    setPendingEventId(restored.pendingEventId);
    if (restored.messages.length > 0) {
      setMessages(restored.messages);
    }
  }, [allowed, isAuthLoading, isBootstrapping, isMainChat, pendingEventId, threadId, token]);

  useEffect(() => {
    if (!token || !allowed || isAuthLoading || isBootstrapping) return;

    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState !== 'active') return;

      void (async () => {
        try {
          await syncLatestThreadState();
          await resumePendingRun();
        } catch {
          // Ignore foreground sync failures and keep current UI state.
        }
      })();
    });

    return () => {
      subscription.remove();
    };
  }, [allowed, isAuthLoading, isBootstrapping, resumePendingRun, syncLatestThreadState, token]);

  useFocusEffect(
    useCallback(() => {
      if (!token || !allowed || isAuthLoading || isBootstrapping) {
        return () => undefined;
      }

      void (async () => {
        try {
          await syncLatestThreadState();
          await resumePendingRun();
        } catch {
          // Keep the currently rendered conversation if a focus refresh fails.
        }
      })();

      return () => undefined;
    }, [allowed, isAuthLoading, isBootstrapping, resumePendingRun, syncLatestThreadState, token]),
  );

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

  const removeComposerMediaAttachment = useCallback((attachmentId: string) => {
    setComposerMediaAttachments((prev) =>
      prev.filter((attachment) => attachment.attachmentId !== attachmentId),
    );
  }, []);

  const addComposerMediaAttachment = useCallback(async () => {
    if (!allowed || isSending) return;
    if (composerMediaAttachments.length >= MAX_CHAT_MEDIA_ATTACHMENTS) {
      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-media-limit`,
          role: 'assistant',
          content: `You can attach up to ${MAX_CHAT_MEDIA_ATTACHMENTS} photos to one /event message.`,
        },
      ]);
      setForceScrollNext(true);
      return;
    }

    try {
      const remainingSlots = MAX_CHAT_MEDIA_ATTACHMENTS - composerMediaAttachments.length;
      const assets = await pickImages({ maxSelection: remainingSlots });
      if (assets.length === 0) {
        return;
      }
      const nextAttachments = await Promise.all(
        assets.map((asset) => buildComposerMediaAttachment(asset)),
      );
      setComposerMediaAttachments((prev) => {
        if (prev.length >= MAX_CHAT_MEDIA_ATTACHMENTS) {
          return prev;
        }
        return [...prev, ...nextAttachments].slice(0, MAX_CHAT_MEDIA_ATTACHMENTS);
      });
      setForceScrollNext(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to attach that photo.';
      showError(message);
      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-media-error`,
          role: 'assistant',
          content: message,
        },
      ]);
      setForceScrollNext(true);
    }
  }, [allowed, composerMediaAttachments.length, isSending, pickImages, showError]);

  const sendMessage = useCallback(
    async (override?: SendMessageInput) => {
      const overrideText = typeof override === 'string' ? override : override?.text;
      const overridePendingCommandId =
        typeof override === 'string' ? undefined : override?.pendingCommandId;
      const uiSubmission = typeof override === 'string' ? undefined : override?.uiSubmission;
      const overrideMediaAttachments =
        typeof override === 'string' ? undefined : override?.mediaAttachments;

      const draft = overrideText ?? input;
      const trimmed = draft.trim();
      const outboundText =
        trimmed || uiSubmission?.text_fallback?.trim() || 'Submitted structured response.';
      const outboundMediaAttachments = overrideMediaAttachments ?? composerMediaAttachments;

      if (
        (!outboundText && outboundMediaAttachments.length === 0) ||
        isSending ||
        !allowed ||
        isBootstrapping
      )
        return;
      Keyboard.dismiss();
      setInput('');
      setComposerMediaAttachments([]);
      setForceScrollNext(true);
      const pendingId = `${Date.now()}-pending`;

      setMessages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-user`,
          role: 'user',
          content: outboundText,
          metadata:
            outboundMediaAttachments.length > 0
              ? {
                  media_attachments: outboundMediaAttachments.map((attachment) => ({
                    attachment_id: attachment.attachmentId,
                    file_name: attachment.fileName,
                    mime_type: attachment.mimeType,
                    source: attachment.source,
                    captured_at: attachment.capturedAt ?? null,
                    width: attachment.width ?? null,
                    height: attachment.height ?? null,
                    uri: attachment.uri,
                  })),
                }
              : undefined,
        },
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
          threadId: isMainChat ? undefined : threadId,
          pendingCommandId: commandsEnabled
            ? overridePendingCommandId !== undefined
              ? overridePendingCommandId
              : pendingEventId
            : null,
          uiSubmission,
          mediaAttachments: outboundMediaAttachments.map(toChatMediaAttachmentPayload),
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
                threadId: isMainChat ? threadId : (threadId ?? initialThreadId),
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
        if (commandResult && typeof commandResult === 'object') {
          const mediaSuggestions = (commandResult as EventCommandResultPayload).media_suggestions;
          if (Array.isArray(mediaSuggestions)) {
            void appendEventPhotoDebugLog('event-media-suggestions-received', {
              previewId: textValue((commandResult as EventCommandResultPayload).preview_id),
              rawCount: mediaSuggestions.length,
              assetIds: mediaSuggestions
                .map((suggestion) => textValue(suggestion?.asset_id))
                .filter(Boolean),
              capturedAt: mediaSuggestions
                .map((suggestion) => nullableText(suggestion?.captured_at))
                .filter(Boolean),
              statuses: mediaSuggestions.map((suggestion) => suggestion?.status ?? null),
            });
          }
        }
        const uiDirectives = response.ui_directives;
        const linkedItems = Array.isArray(response.linked_items)
          ? (response.linked_items as LinkedItem[])
          : [];
        const generatedFiles = Array.isArray(response.generated_files)
          ? (response.generated_files as GeneratedFile[])
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
                    commandResult ||
                    uiDirectives ||
                    linkedItems.length > 0 ||
                    generatedFiles.length > 0
                      ? {
                          command_result: commandResult,
                          ui_directives: uiDirectives,
                          linked_items: linkedItems.length > 0 ? linkedItems : undefined,
                          generated_files: generatedFiles.length > 0 ? generatedFiles : undefined,
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
          if (outboundMediaAttachments.length > 0) {
            setComposerMediaAttachments(outboundMediaAttachments);
          }
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
        const keepPendingRun = Boolean(activeRunId) && shouldKeepPendingRun(error);
        if (!keepPendingRun) {
          await clearPendingRun();
          if (outboundMediaAttachments.length > 0) {
            setComposerMediaAttachments(outboundMediaAttachments);
          }
        }
        setForceScrollNext(true);
        setMessages((prev) =>
          prev.map((message) =>
            message.id === pendingId
              ? {
                  ...message,
                  content: keepPendingRun
                    ? 'Reconnecting...'
                    : conciseBackendErrorMessage(
                        error,
                        'I hit a snag reaching the brain. Try again in a moment.',
                      ),
                  pending: keepPendingRun,
                  metadata: keepPendingRun
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
    },
    [
      allowed,
      composerMediaAttachments,
      commandsEnabled,
      input,
      initialThreadId,
      isBootstrapping,
      isMainChat,
      isSending,
      pendingEventId,
      signOut,
      threadId,
      token,
    ],
  );

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

  const applyContactDraftEdits = useCallback(
    (
      previewId: string,
      baseDraft: ReturnType<typeof buildContactDraft>,
      nextDraft: ReturnType<typeof buildContactDraft>,
    ) => {
      if (!baseDraft || !nextDraft) return;
      const modifications = buildContactDraftModifications(baseDraft, nextDraft);
      const modifiedFields = contactDraftModificationSummary(modifications);

      setContactDraftModificationsByPreview((prev) => {
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

  useFocusEffect(
    useCallback(() => {
      if (!activeContactDraftEditorSessionId) {
        return () => undefined;
      }

      const result = consumeContactDraftEditResult(activeContactDraftEditorSessionId);
      if (result) {
        applyContactDraftEdits(result.previewId, result.baseDraft, result.nextDraft);
        setActiveContactDraftEditorSessionId(null);
      }

      return () => undefined;
    }, [activeContactDraftEditorSessionId, applyContactDraftEdits]),
  );

  const handleDirectiveSubmission = useCallback(
    async (
      messageId: string,
      directives: UiDirectives | undefined,
      submission: UiSubmissionInput,
      commandResult: CommandResult | undefined,
    ) => {
      if (submission.action_id === CONTACT_CONFIRM_ACTION_ID) {
        const action = parseContactAction(submission.values?.['option_id']);
        if (!action) {
          return;
        }

        if (action.type === 'edit') {
          const loadedContacts = await loadEventEditorContacts();
          const loadedPlaces = await loadEventEditorPlaces();
          const baseDraft = buildContactDraft(commandResult, action.previewId);
          if (!baseDraft) {
            setMessages((prev) => [
              ...prev,
              {
                id: `${Date.now()}-contact-edit-unavailable`,
                role: 'assistant',
                content:
                  'I could not load that contact draft for editing. Please retry from the latest preview.',
              },
            ]);
            setForceScrollNext(true);
            return;
          }
          const existingModifications = contactDraftModificationsByPreview[action.previewId];
          const session = createContactDraftEditSession({
            previewId: action.previewId,
            baseDraft,
            initialDraft: applyContactDraftModifications(baseDraft, existingModifications),
            availableContacts: loadedContacts,
            availablePlaces: loadedPlaces,
          });
          setActiveContactDraftEditorSessionId(session.sessionId);
          router.push({
            pathname: '/contacts/proposals/[previewId]',
            params: {
              previewId: action.previewId,
              draftSessionId: session.sessionId,
            },
          });
          return;
        }

        setIsConfirmingEvent(true);
        try {
          await apiFetch('/mobile/commands/contact/confirm', {
            method: 'POST',
            body: JSON.stringify({
              preview_id: action.previewId,
              confirmed: action.type === 'confirm',
              modifications: contactDraftModificationsByPreview[action.previewId] || {},
            }),
            token,
          });

          const resolved: CommandResolvedMeta = {
            status: action.type === 'confirm' ? 'created' : 'cancelled',
            label:
              action.type === 'confirm' ? 'Contact changes applied' : 'Contact update cancelled',
          };
          setPendingEventId(null);
          setContactDraftModificationsByPreview((prev) => {
            if (!prev[action.previewId]) return prev;
            const next = { ...prev };
            delete next[action.previewId];
            return next;
          });
          setMessages((prev) =>
            prev.map((message) =>
              message.id === messageId
                ? {
                    ...message,
                    metadata: {
                      ...message.metadata,
                      command_resolved: resolved,
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
              id: `${Date.now()}-contact-action-error`,
              role: 'assistant',
              content: expired
                ? 'This contact draft expired. Please run /contact again.'
                : 'I could not complete that contact action right now.',
            },
          ]);
          setForceScrollNext(true);
          return;
        } finally {
          setIsConfirmingEvent(false);
        }
      }

      if (submission.action_id?.startsWith(CONTACT_EDIT_ACTION_PREFIX)) {
        const previewId = submission.action_id
          .slice(CONTACT_EDIT_ACTION_PREFIX.length)
          .replace(/^:/, '')
          .trim();
        if (!previewId) {
          return;
        }

        setIsConfirmingEvent(true);
        try {
          await apiFetch('/mobile/commands/contact/confirm', {
            method: 'POST',
            body: JSON.stringify({
              preview_id: previewId,
              confirmed: true,
              modifications: submission.values || {},
            }),
            token,
          });

          const resolved: CommandResolvedMeta = {
            status: 'created',
            label: 'Contact changes applied',
          };
          setPendingEventId(null);
          setMessages((prev) =>
            prev.map((message) =>
              message.id === messageId
                ? {
                    ...message,
                    metadata: {
                      ...message.metadata,
                      command_resolved: resolved,
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
              id: `${Date.now()}-contact-edit-error`,
              role: 'assistant',
              content: expired
                ? 'This contact draft expired. Please run /contact again.'
                : 'I could not apply those contact edits right now.',
            },
          ]);
          setForceScrollNext(true);
          return;
        } finally {
          setIsConfirmingEvent(false);
        }
      }

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
                content:
                  'I could not load that draft for editing. Please retry from the latest event preview.',
              },
            ]);
            setForceScrollNext(true);
            return;
          }

          const existingModifications = eventDraftModificationsByPreview[action.previewId];
          const candidateEvents = buildEventCandidateList(
            (commandResult as EventCommandResultPayload | undefined)?.candidate_events,
          );
          const createFallbackDraft = buildCreateFallbackEventDraft(
            commandResult,
            action.previewId,
          );
          const session = createEventDraftEditSession({
            previewId: action.previewId,
            baseDraft,
            initialDraft: applyDraftModifications(
              baseDraft,
              existingModifications,
              contactNameById,
              candidateEvents,
            ),
            createFallbackDraft,
            availableContacts: loadedContacts,
            availablePlaces: loadedPlaces,
            candidateEvents,
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
              ? new Set(
                  baseDraftForConfirm.participants.map((participant) => participant.contactId),
                )
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
          const confirmedOperation =
            modifications.operation ??
            (baseDraftForConfirm ? baseDraftForConfirm.operation : 'create');
          const resolvedStatus =
            action.type !== 'confirm'
              ? 'cancelled'
              : confirmedOperation === 'update'
                ? 'updated'
                : 'created';
          const resolvedLabel =
            resolvedStatus === 'created'
              ? 'Event created'
              : resolvedStatus === 'updated'
                ? 'Event updated'
                : 'Event cancelled';
          const resolved: CommandResolvedMeta = {
            status: resolvedStatus,
            label: resolvedLabel,
          };
          setMessages((prev) =>
            prev.map((message) =>
              message.id === messageId
                ? {
                    ...message,
                    metadata: {
                      ...message.metadata,
                      command_resolved: resolved,
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
          const fallbackMessage = expired
            ? 'This event draft expired. Please run /event again.'
            : 'I could not complete that event action right now.';
          console.error('[brain] event confirm failed', error);
          setMessages((prev) => [
            ...prev,
            {
              id: `${Date.now()}-event-action-error`,
              role: 'assistant',
              content: conciseBackendErrorMessage(error, fallbackMessage),
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
        const clarificationId = clarificationIdFromAction(
          submission.action_id,
          EVENT_CLARIFICATION_ACTION_PREFIX,
        );
        void sendMessage({
          text: answer,
          pendingCommandId: clarificationId ?? pendingEventId,
          uiSubmission: submission,
        });
        return;
      }

      const isContactClarificationSubmission = submission.action_id?.startsWith(
        CONTACT_CLARIFICATION_ACTION_PREFIX,
      );

      if (isContactClarificationSubmission) {
        const answer = buildEventClarificationAnswer(submission, directives);
        if (!answer) {
          return;
        }
        const clarificationId = clarificationIdFromAction(
          submission.action_id,
          CONTACT_CLARIFICATION_ACTION_PREFIX,
        );
        void sendMessage({
          text: answer,
          pendingCommandId: clarificationId ?? pendingEventId,
          uiSubmission: submission,
        });
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
      contactDraftModificationsByPreview,
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
  const showSlashPalette = commandsEnabled && trimmedInput.startsWith('/') && !hasCommandToken;
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

  const handleGeneratedFilePress = useCallback(
    async (file: GeneratedFile) => {
      if (!token) {
        showError('Session expired. Sign in again.');
        return;
      }

      try {
        const result = await downloadGeneratedFile(file, token, refreshToken);
        if (result.fallbackWarning) {
          showError(result.fallbackWarning);
        }
        showSuccess(
          result.savedToDigitalBrainFolder
            ? `Saved ${result.label} to your Digital Brain folder.`
            : `Downloaded ${result.label}.`,
        );
        void Linking.openURL(result.openUri).catch(() => undefined);
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to download this file.';
        showError(message);
      }
    },
    [refreshToken, showError, showSuccess, token],
  );

  const scrollToBottom = useCallback((animated: boolean) => {
    if (scrollFallbackTimeoutRef.current) {
      clearTimeout(scrollFallbackTimeoutRef.current);
    }

    const runScroll = () => {
      listRef.current?.scrollToEnd({ animated });
    };

    requestAnimationFrame(() => {
      requestAnimationFrame(runScroll);
    });

    scrollFallbackTimeoutRef.current = setTimeout(runScroll, animated ? 140 : 0);
  }, []);

  useEffect(() => {
    return () => {
      if (scrollFallbackTimeoutRef.current) {
        clearTimeout(scrollFallbackTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!listRef.current) return;
    if (!isAtBottomRef.current && !forceScrollNext) return;

    scrollToBottom(forceScrollNext);
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
    scrollToBottom,
  ]);

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <KeyboardAvoidingView
        style={[styles.screen, !isMainChat && styles.threadScreen]}
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
            scrollToBottom(forceScrollNext);
          }}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode={Platform.OS === 'ios' ? 'interactive' : 'on-drag'}
          ListFooterComponent={<View style={{ height: listBottomInset }} />}
          contentContainerStyle={[
            styles.listContent,
            {
              paddingTop: isMainChat
                ? insets.top + COLLAPSING_TOP_BAR_HEIGHT + COLLAPSING_CONTENT_TOP_PADDING
                : insets.top + COLLAPSING_TOP_BAR_HEIGHT - 8,
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
            const eventPreviewId = extractEventPreviewId(commandResult);
            const contactPreviewId = extractContactPreviewId(commandResult);
            const previewId = eventPreviewId || contactPreviewId;
            const isSupersededEventCard = Boolean(
              previewId && pendingEventId && previewId !== pendingEventId,
            );
            const eventPreviewModifications = eventPreviewId
              ? eventDraftModificationsByPreview[eventPreviewId]
              : undefined;
            const contactPreviewModifications = contactPreviewId
              ? contactDraftModificationsByPreview[contactPreviewId]
              : undefined;
            const directives = item.metadata?.ui_directives;
            const linkedItems = item.metadata?.linked_items || [];
            const generatedFiles = item.metadata?.generated_files || [];
            let directivesForCard = directives;
            if (contactPreviewId && directivesForCard) {
              directivesForCard = pruneContactPreviewBlocks(directivesForCard, contactPreviewId);
            }
            if (eventPreviewId && directives && eventPreviewModifications) {
              const baseDraft = buildEventDraft(commandResult, eventPreviewId);
              if (baseDraft) {
                const contactNameById = new Map(
                  eventEditorContacts.map((contact) => [contact.contact_id, contact.display_name]),
                );
                for (const participant of baseDraft.participants) {
                  if (!contactNameById.has(participant.contactId)) {
                    contactNameById.set(participant.contactId, participant.displayName);
                  }
                }
                const candidateEvents = buildEventCandidateList(
                  (commandResult as EventCommandResultPayload | undefined)?.candidate_events,
                );
                const modifiedDraft = applyDraftModifications(
                  baseDraft,
                  eventPreviewModifications,
                  contactNameById,
                  candidateEvents,
                );
                const withUpdatedPreview = updateEventPreviewCard(
                  directives,
                  eventPreviewId,
                  modifiedDraft,
                );
                const withUpdatedMatchCards = updateEventMatchCards(
                  withUpdatedPreview,
                  eventPreviewId,
                  modifiedDraft,
                  candidateEvents,
                );
                directivesForCard = updateEventAuxiliaryCards(
                  withUpdatedMatchCards,
                  commandResult,
                  eventPreviewId,
                  modifiedDraft,
                );
              }
            }
            if (contactPreviewId && directivesForCard && contactPreviewModifications) {
              const baseDraft = buildContactDraft(commandResult, contactPreviewId);
              if (baseDraft) {
                const modifiedDraft = applyContactDraftModifications(
                  baseDraft,
                  contactPreviewModifications,
                );
                directivesForCard = updateContactDraftCards(
                  directivesForCard,
                  contactPreviewId,
                  modifiedDraft,
                );
              }
            }
            const requestError = item.metadata?.request_error;
            const userMediaAttachments = item.metadata?.media_attachments || [];
            const isErrorExpanded = Boolean(expandedErrorMessageIds[item.id]);
            const clarificationDirectiveId = extractClarificationDirectiveId(directivesForCard);
            const isLatestAssistantMessage = item.id === lastMessage?.id;
            const isStaleClarificationCard = Boolean(
              clarificationDirectiveId &&
              isClarificationDirective(directivesForCard) &&
              pendingEventId &&
              pendingEventId !== clarificationDirectiveId &&
              !isLatestAssistantMessage,
            );
            const eventMediaDraft = eventPreviewId
              ? (() => {
                  const baseDraft = buildEventDraft(commandResult, eventPreviewId);
                  if (!baseDraft) return null;
                  if (!eventPreviewModifications) return baseDraft;
                  return applyDraftModifications(
                    baseDraft,
                    eventPreviewModifications,
                    new Map(),
                    buildEventCandidateList(
                      (commandResult as EventCommandResultPayload | undefined)?.candidate_events,
                    ),
                  );
                })()
              : null;

            return (
              <View
                style={[
                  styles.messageBubble,
                  item.role === 'user' ? styles.userBubble : styles.assistantBubble,
                ]}
              >
                {item.role === 'user' && userMediaAttachments.length > 0 ? (
                  <View style={styles.userMediaRow}>
                    {userMediaAttachments.slice(0, 4).map((attachment, index) =>
                      attachment.uri ? (
                        <Image
                          key={`${item.id}:media:${attachment.attachment_id || index}`}
                          source={{ uri: attachment.uri }}
                          style={styles.userMediaImage}
                          resizeMode="cover"
                        />
                      ) : (
                        <View
                          key={`${item.id}:media:${attachment.attachment_id || index}`}
                          style={[styles.userMediaImage, styles.userMediaFallback]}
                        >
                          <Ionicons name="image-outline" size={18} color="#fff" />
                        </View>
                      ),
                    )}
                    {userMediaAttachments.length > 4 ? (
                      <View style={[styles.userMediaImage, styles.userMediaOverflow]}>
                        <Text
                          style={styles.userMediaOverflowText}
                        >{`+${userMediaAttachments.length - 4}`}</Text>
                      </View>
                    ) : null}
                  </View>
                ) : null}
                {item.role === 'assistant' ? (
                  <View style={styles.markdownContainer}>
                    {renderAssistantMarkdown(item.content, item.id)}
                  </View>
                ) : (
                  <Text style={[styles.messageText, styles.userText]} selectable>
                    {item.content}
                  </Text>
                )}
                {directivesForCard && !isStaleClarificationCard && (
                  <View style={styles.commandCardWrap}>
                    {eventMediaDraft && eventMediaDraft.mediaSuggestions.length > 0 ? (
                      <EventMediaSuggestionCard
                        suggestions={eventMediaDraft.mediaSuggestions}
                        token={token}
                        editable={!item.metadata?.command_resolved && !isSupersededEventCard}
                        onRemove={(assetId) => {
                          if (isSupersededEventCard) return;
                          setEventDraftModificationsByPreview((current) => {
                            const baseDraft = buildEventDraft(commandResult, eventPreviewId || '');
                            if (!baseDraft) return current;
                            const previous = current[eventPreviewId || ''] || {};
                            const selectedIds =
                              previous.media_asset_ids ??
                              baseDraft.mediaSuggestions.map((suggestion) => suggestion.asset_id);
                            return {
                              ...current,
                              [eventPreviewId || '']: {
                                ...previous,
                                media_asset_ids: selectedIds.filter((id) => id !== assetId),
                              },
                            };
                          });
                        }}
                      />
                    ) : null}
                    <UiDirectiveCard
                      directives={directivesForCard}
                      isSubmitting={isSending || isConfirmingEvent || isSupersededEventCard}
                      resolved={item.metadata?.command_resolved}
                      onFieldFocus={() => {
                        setForceScrollNext(true);
                      }}
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
                {generatedFiles.length > 0 ? (
                  <GeneratedFilesRow
                    files={generatedFiles}
                    disabled={item.pending || isSending}
                    onPressFile={handleGeneratedFilePress}
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
          title={isMainChat ? 'Brain' : undefined}
          secondaryTitle={isMainChat ? 'Talk to "your" memory' : undefined}
          scrollY={scrollY}
          profileName={name || email || 'You'}
          profilePhoto={photo}
          token={token}
          onPressProfile={isMainChat ? () => router.push('/settings') : undefined}
          onPressBack={!isMainChat ? () => router.back() : undefined}
          rightAccessory={
            isMainChat ? (
              <Pressable
                onPress={() => router.push('/chat/history')}
                accessibilityRole="button"
                accessibilityLabel="Open thread history"
                style={({ pressed }) => [
                  styles.headerActionButton,
                  pressed && styles.headerActionButtonPressed,
                ]}
              >
                <Ionicons name="time-outline" size={18} color={theme.colors.ink} />
              </Pressable>
            ) : undefined
          }
        />
        {showAnchoredSlashPalette && (
          <View
            style={[
              styles.slashPaletteAnchor,
              { bottom: composerHeight + composerBottomOffset + 8 },
            ]}
          >
            <SlashCommandPalette
              query={slashQuery}
              onSelect={(command) => {
                const nextInput = `/${command} `;
                setInput(nextInput);
              }}
            />
          </View>
        )}

        <ChatComposer
          attachments={composerMediaAttachments}
          onRemoveAttachment={removeComposerMediaAttachment}
          commandsEnabled={commandsEnabled}
          allowed={allowed}
          isSending={isSending}
          canSend={canSend}
          input={input}
          inputRef={inputRef}
          placeholder={commandsEnabled ? 'Ask me anything...' : 'Reply to this thread...'}
          minInputHeight={MIN_CHAT_INPUT_HEIGHT}
          maxInputHeight={MAX_CHAT_INPUT_HEIGHT}
          composerBottomOffset={composerBottomOffset}
          composerPaddingBottom={keyboardVisible ? 24 : tabBarClearance + (isMainChat ? 8 : 4)}
          onComposerHeightChange={setComposerHeight}
          onChangeInput={setInput}
          onSend={() => {
            void sendMessage();
          }}
          onAttachPhoto={() => {
            void addComposerMediaAttachment();
          }}
          onInputFocus={() => {
            setForceScrollNext(true);
          }}
          onInputBlur={() => {
            if (Platform.OS === 'android') {
              setKeyboardVisible(false);
              setKeyboardHeight(0);
            }
          }}
          onErrorMessage={showError}
          attachDisabled={
            !allowed || isSending || composerMediaAttachments.length >= MAX_CHAT_MEDIA_ATTACHMENTS
          }
        />
      </KeyboardAvoidingView>
      {imagePickerSheet}
    </LinearGradient>
  );
}

export default function ChatScreen() {
  const tabBarHeight = useBottomTabBarHeight();
  return <ChatConversationScreen tabBarHeight={tabBarHeight} />;
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  screen: {
    flex: 1,
    paddingTop: COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT + 20,
  },
  threadScreen: {
    paddingTop: 0,
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
  userMediaRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  userMediaImage: {
    width: 68,
    height: 68,
    borderRadius: 16,
    backgroundColor: 'rgba(255,255,255,0.18)',
  },
  userMediaFallback: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  userMediaOverflow: {
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.22)',
  },
  userMediaOverflowText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
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
  headerActionButton: {
    minHeight: 40,
    minWidth: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: 'rgba(255, 255, 255, 0.92)',
  },
  headerActionButtonPressed: {
    opacity: 0.75,
  },
});
