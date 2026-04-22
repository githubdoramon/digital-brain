export type EntityKind = 'contacts' | 'places' | 'events';

export type EntityFilters = {
  contactIds: string[];
  placeIds: string[];
  eventIds: string[];
};

export type EntityFilterOption = {
  id: string;
  kind: EntityKind;
  label: string;
  description?: string | null;
};

export type Relationship = {
  relationship_id: string;
  contact_id: string;
  type: string;
  other_type: string | null;
  direction: 'incoming' | 'outgoing';
};

export type ContactListItem = {
  contact_id: string;
  display_name: string;
  aliases?: string[];
  emails: string[];
  phones: string[];
  tags: string[];
  comments: string;
  external_id: string | null;
  avatar_url?: string | null;
  relationships: Relationship[];
};

export type PlaceListItem = {
  place_id: string;
  name: string | null;
  aliases: string[];
  description: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  lat: number | null;
  lon: number | null;
};

export type EventListItem = {
  id: string;
  title?: string | null;
  summary?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

export type EventSearchResponse = {
  events: EventListItem[];
  has_more?: boolean;
  next_offset?: number;
};

export const EMPTY_ENTITY_FILTERS: EntityFilters = {
  contactIds: [],
  placeIds: [],
  eventIds: [],
};

export const ENTITY_META: Record<
  EntityKind,
  { label: string; icon: 'people-outline' | 'location-outline' | 'sparkles-outline'; placeholder: string }
> = {
  contacts: {
    label: 'Contacts',
    icon: 'people-outline',
    placeholder: 'Search contacts',
  },
  places: {
    label: 'Places',
    icon: 'location-outline',
    placeholder: 'Search places',
  },
  events: {
    label: 'Events',
    icon: 'sparkles-outline',
    placeholder: 'Search events',
  },
};
