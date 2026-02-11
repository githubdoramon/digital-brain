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

type EventDraftStore = {
  sessions: Map<string, EventDraftEditSession>;
  results: Map<string, EventDraftEditResult>;
};

function getStore(): EventDraftStore {
  const key = '__eventDraftEditorStore';
  const globalObj = globalThis as typeof globalThis & {
    __eventDraftEditorStore?: EventDraftStore;
  };
  if (!globalObj[key]) {
    globalObj[key] = {
      sessions: new Map<string, EventDraftEditSession>(),
      results: new Map<string, EventDraftEditResult>(),
    };
  }
  return globalObj[key]!;
}

function nextSessionId() {
  return `event_draft_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

export function createEventDraftEditSession(
  input: Omit<EventDraftEditSession, 'sessionId'>,
): EventDraftEditSession {
  const store = getStore();
  const sessionId = nextSessionId();
  const session: EventDraftEditSession = { ...input, sessionId };
  store.sessions.set(sessionId, session);
  console.info('[event-draft-session] create', {
    sessionId,
    previewId: session.previewId,
    contactCount: session.availableContacts.length,
  });
  return session;
}

export function getEventDraftEditSession(sessionId: string | null | undefined) {
  if (!sessionId) return null;
  const store = getStore();
  const session = store.sessions.get(sessionId) ?? null;
  console.info('[event-draft-session] get', {
    sessionId,
    found: Boolean(session),
  });
  return session;
}

export function submitEventDraftEditSession(
  sessionId: string | null | undefined,
  nextDraft: EventDraft,
): EventDraftEditResult | null {
  if (!sessionId) return null;
  const store = getStore();
  const session = store.sessions.get(sessionId);
  if (!session) return null;
  const result: EventDraftEditResult = {
    sessionId,
    previewId: session.previewId,
    baseDraft: session.baseDraft,
    nextDraft,
  };
  store.results.set(sessionId, result);
  console.info('[event-draft-session] submit', {
    sessionId,
    previewId: session.previewId,
  });
  return result;
}

export function consumeEventDraftEditResult(
  sessionId: string | null | undefined,
): EventDraftEditResult | null {
  if (!sessionId) return null;
  const store = getStore();
  const result = store.results.get(sessionId) ?? null;
  if (result) {
    store.results.delete(sessionId);
    store.sessions.delete(sessionId);
  }
  console.info('[event-draft-session] consume', {
    sessionId,
    found: Boolean(result),
  });
  return result;
}

export function clearEventDraftEditSession(sessionId: string | null | undefined) {
  if (!sessionId) return;
  const store = getStore();
  store.results.delete(sessionId);
  store.sessions.delete(sessionId);
  console.info('[event-draft-session] clear', { sessionId });
}
