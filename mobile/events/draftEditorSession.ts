import type { EventContactOption, EventDraft } from '@/components/event-draft/types';

export type EventDraftEditSession = {
  sessionId: string;
  previewId: string;
  baseDraft: EventDraft;
  initialDraft: EventDraft;
  availableContacts: EventContactOption[];
};

export type EventDraftEditResult = {
  sessionId: string;
  previewId: string;
  baseDraft: EventDraft;
  nextDraft: EventDraft;
};

const editSessions = new Map<string, EventDraftEditSession>();
const editResults = new Map<string, EventDraftEditResult>();

function nextSessionId() {
  return `event_draft_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

export function createEventDraftEditSession(
  input: Omit<EventDraftEditSession, 'sessionId'>,
): EventDraftEditSession {
  const sessionId = nextSessionId();
  const session: EventDraftEditSession = { ...input, sessionId };
  editSessions.set(sessionId, session);
  return session;
}

export function getEventDraftEditSession(sessionId: string | null | undefined) {
  if (!sessionId) return null;
  return editSessions.get(sessionId) ?? null;
}

export function submitEventDraftEditSession(
  sessionId: string | null | undefined,
  nextDraft: EventDraft,
): EventDraftEditResult | null {
  if (!sessionId) return null;
  const session = editSessions.get(sessionId);
  if (!session) return null;
  const result: EventDraftEditResult = {
    sessionId,
    previewId: session.previewId,
    baseDraft: session.baseDraft,
    nextDraft,
  };
  editResults.set(sessionId, result);
  return result;
}

export function consumeEventDraftEditResult(
  sessionId: string | null | undefined,
): EventDraftEditResult | null {
  if (!sessionId) return null;
  const result = editResults.get(sessionId) ?? null;
  if (result) {
    editResults.delete(sessionId);
  }
  editSessions.delete(sessionId);
  return result;
}

export function clearEventDraftEditSession(sessionId: string | null | undefined) {
  if (!sessionId) return;
  editResults.delete(sessionId);
  editSessions.delete(sessionId);
}
