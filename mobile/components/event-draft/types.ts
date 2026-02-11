export type EventDraftParticipant = {
  contactId: string;
  displayName: string;
};

export type EventContactOption = {
  contact_id: string;
  display_name: string;
};

export type EventDraft = {
  title: string;
  summary: string;
  when: string;
  where: string;
  tags: string[];
  types: string[];
  participants: EventDraftParticipant[];
};

export type EventDraftModifications = {
  title?: string;
  summary?: string;
  when?: string | null;
  where?: string;
  tags?: string[];
  types?: string[];
  contact_ids?: string[];
};

export const EMPTY_EVENT_DRAFT: EventDraft = {
  title: '',
  summary: '',
  when: '',
  where: '',
  tags: [],
  types: [],
  participants: [],
};
