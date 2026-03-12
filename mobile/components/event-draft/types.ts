export type EventDraftParticipant = {
  contactId: string;
  displayName: string;
};

export type EventContactOption = {
  contact_id: string;
  display_name: string;
  aliases?: string[];
};

export type EventPlaceOption = {
  place_id: string;
  name: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  aliases?: string[];
};

export type EventDraft = {
  title: string;
  summary: string;
  when: string;
  endWhen: string;
  where: string;
  placeId?: string | null;
  tags: string[];
  types: string[];
  participants: EventDraftParticipant[];
};

export type EventDraftModifications = {
  title?: string;
  summary?: string;
  when?: string | null;
  end_when?: string | null;
  where?: string;
  place_id?: string | null;
  tags?: string[];
  types?: string[];
  contact_ids?: string[];
};

export const EMPTY_EVENT_DRAFT: EventDraft = {
  title: '',
  summary: '',
  when: '',
  endWhen: '',
  where: '',
  placeId: null,
  tags: [],
  types: [],
  participants: [],
};
