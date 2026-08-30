export type EventDraftParticipant = {
  contactId: string;
  displayName: string;
};

export type EventPhotoTaggedContact = {
  contact_id: string;
  display_name: string;
};

export type EventPhotoDetectedPerson = {
  person_id: string;
  name?: string | null;
  contact_id?: string | null;
  display_name: string;
  has_contact_match?: boolean;
};

export type EventPhoto = {
  asset_id: string;
  media_type?: string | null;
  checksum?: string | null;
  file_name?: string | null;
  mime_type?: string | null;
  captured_at?: string | null;
  local_asset_id?: string | null;
  source?: string | null;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  distance_m?: number | null;
  temporal_distance_seconds?: number | null;
  has_gps?: boolean;
  status?: 'included' | 'removed' | string | null;
  match_reasons?: string[];
  created_at?: string | null;
  updated_at?: string | null;
  thumbnail_path?: string | null;
  tagged_contacts?: EventPhotoTaggedContact[];
  detected_people?: EventPhotoDetectedPerson[];
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

export type EventDraftOperation = 'create' | 'update';

export type EventMatchCandidate = {
  eventId: string;
  title: string;
  summary: string;
  startDate: string | null;
  endDate: string | null;
  place: {
    placeId: string;
    name: string;
    city?: string | null;
    country?: string | null;
  } | null;
  matchScore: number;
  matchSources: string[];
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
  operation: EventDraftOperation;
  existingEventId: string | null;
  matchedEvent: EventMatchCandidate | null;
  mediaSuggestions: EventPhoto[];
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
  operation?: EventDraftOperation;
  existing_event_id?: string | null;
  media_asset_ids?: string[];
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
  operation: 'create',
  existingEventId: null,
  matchedEvent: null,
  mediaSuggestions: [],
};
