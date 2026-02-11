export type EventDraft = {
  title: string;
  summary: string;
  when: string;
  where: string;
  tags: string[];
  types: string[];
};

export type EventDraftModifications = {
  title?: string;
  summary?: string;
  when?: string | null;
  where?: string;
  tags?: string[];
  types?: string[];
};

export const EMPTY_EVENT_DRAFT: EventDraft = {
  title: '',
  summary: '',
  when: '',
  where: '',
  tags: [],
  types: [],
};
