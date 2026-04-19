import type { ContactProposalDraft } from '@/contact-draft/types';
import type { EventContactOption, EventPlaceOption } from '@/components/event-draft/types';

export type ContactDraftEditSession = {
  sessionId: string;
  previewId: string;
  baseDraft: ContactProposalDraft;
  initialDraft: ContactProposalDraft;
  availableContacts: EventContactOption[];
  availablePlaces: EventPlaceOption[];
};

export type ContactDraftEditResult = {
  sessionId: string;
  previewId: string;
  baseDraft: ContactProposalDraft;
  nextDraft: ContactProposalDraft;
};

type ContactDraftStore = {
  sessions: Map<string, ContactDraftEditSession>;
  results: Map<string, ContactDraftEditResult>;
};

function getStore(): ContactDraftStore {
  const key = '__contactDraftEditorStore';
  const globalObj = globalThis as typeof globalThis & {
    __contactDraftEditorStore?: ContactDraftStore;
  };
  if (!globalObj[key]) {
    globalObj[key] = {
      sessions: new Map<string, ContactDraftEditSession>(),
      results: new Map<string, ContactDraftEditResult>(),
    };
  }
  return globalObj[key]!;
}

function nextSessionId() {
  return `contact_draft_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

export function createContactDraftEditSession(
  input: Omit<ContactDraftEditSession, 'sessionId'>,
): ContactDraftEditSession {
  const store = getStore();
  const sessionId = nextSessionId();
  const session: ContactDraftEditSession = { ...input, sessionId };
  store.sessions.set(sessionId, session);
  return session;
}

export function getContactDraftEditSession(sessionId: string | null | undefined) {
  if (!sessionId) return null;
  return getStore().sessions.get(sessionId) ?? null;
}

export function submitContactDraftEditSession(
  sessionId: string | null | undefined,
  nextDraft: ContactProposalDraft,
): ContactDraftEditResult | null {
  if (!sessionId) return null;
  const store = getStore();
  const session = store.sessions.get(sessionId);
  if (!session) return null;
  const result: ContactDraftEditResult = {
    sessionId,
    previewId: session.previewId,
    baseDraft: session.baseDraft,
    nextDraft,
  };
  store.results.set(sessionId, result);
  return result;
}

export function consumeContactDraftEditResult(sessionId: string | null | undefined) {
  if (!sessionId) return null;
  const store = getStore();
  const result = store.results.get(sessionId) ?? null;
  if (result) {
    store.results.delete(sessionId);
    store.sessions.delete(sessionId);
  }
  return result;
}

export function clearContactDraftEditSession(sessionId: string | null | undefined) {
  if (!sessionId) return;
  const store = getStore();
  store.results.delete(sessionId);
  store.sessions.delete(sessionId);
}
