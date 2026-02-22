import { useLocalSearchParams } from 'expo-router';
import React from 'react';
import { Alert, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import {
  EventDetailsForm,
  EventDraftEditorScreen,
} from '@/components/event-draft/EventDraftEditorScreen';
import type { EventDraft, EventPlaceOption } from '@/components/event-draft/types';
import { theme } from '@/theme';

type EventDetail = {
  id: string;
  title?: string | null;
  summary?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  tags?: string[] | null;
  people?: (string | { contact_id?: string | null; display_name?: string | null })[] | null;
  place?: {
    place_id: string;
    name?: string | null;
    city?: string | null;
    country?: string | null;
  } | null;
  types?: string[] | null;
  external_id?: string | null;
};

type Contact = {
  contact_id: string;
  display_name: string;
  aliases?: string[];
};

type RouteParams = {
  eventId: string;
  draftSessionId?: string;
  editable?: string;
};

type PlaceListResponse = {
  places: EventPlaceOption[];
};

function isEditableParam(value: string | undefined): boolean {
  if (!value) return false;
  return value === '1' || value.toLowerCase() === 'true';
}

function formatDateRange(start?: string | null, end?: string | null) {
  if (!start) return 'Date TBD';
  const startDate = new Date(start);
  const startLabel = Number.isNaN(startDate.getTime())
    ? start
    : startDate.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
  if (!end) return startLabel;
  const endDate = new Date(end);
  const endLabel = Number.isNaN(endDate.getTime())
    ? end
    : endDate.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
  return `${startLabel} – ${endLabel}`;
}

type EventPerson = {
  contactId: string;
  displayName: string;
};

function normalizeEventPeople(
  people: EventDetail['people'],
  contactMap: Map<string, string>,
): EventPerson[] {
  if (!Array.isArray(people)) return [];

  const uniqueByContactId = new Map<string, EventPerson>();
  for (const person of people) {
    if (typeof person === 'string') {
      const contactId = person.trim();
      if (!contactId || uniqueByContactId.has(contactId)) continue;
      uniqueByContactId.set(contactId, {
        contactId,
        displayName: contactMap.get(contactId) || contactId,
      });
      continue;
    }

    if (!person || typeof person !== 'object') continue;

    const contactId = String(person.contact_id || '').trim();
    if (!contactId || uniqueByContactId.has(contactId)) continue;

    const rawDisplayName = String(person.display_name || '').trim();
    uniqueByContactId.set(contactId, {
      contactId,
      displayName: rawDisplayName || contactMap.get(contactId) || contactId,
    });
  }

  return Array.from(uniqueByContactId.values());
}

function toDraft(event: EventDetail, contactMap: Map<string, string>): EventDraft {
  const placeLabel = [event.place?.name, event.place?.city, event.place?.country]
    .map((value) => String(value || '').trim())
    .filter(Boolean)
    .join(', ');

  return {
    title: String(event.title || '').trim(),
    summary: String(event.summary || '').trim(),
    when: String(event.start_date || '').trim(),
    where: placeLabel,
    placeId: event.place?.place_id || null,
    tags: Array.isArray(event.tags) ? event.tags.map((tag) => String(tag || '').trim()).filter(Boolean) : [],
    types: Array.isArray(event.types) ? event.types.map((typeValue) => String(typeValue || '').trim()).filter(Boolean) : [],
    participants: normalizeEventPeople(event.people, contactMap),
  };
}

export default function EventScreenRoute() {
  const params = useLocalSearchParams<RouteParams>();
  const eventId = Array.isArray(params.eventId) ? params.eventId[0] : params.eventId;
  const draftSessionId = Array.isArray(params.draftSessionId)
    ? params.draftSessionId[0]
    : params.draftSessionId;
  const editableParam = Array.isArray(params.editable) ? params.editable[0] : params.editable;
  const editable = isEditableParam(editableParam);

  React.useEffect(() => {
    console.info('[event-draft-session] event-route-params', {
      eventId,
      draftSessionId: draftSessionId ?? null,
    });
  }, [draftSessionId, eventId]);

  if (draftSessionId) {
    return <EventDraftEditorScreen sessionId={draftSessionId} />;
  }

  return <EventDetailView eventId={eventId} editable={editable} />;
}

type EventDetailViewProps = {
  eventId?: string;
  editable: boolean;
};

function EventDetailView({ eventId, editable }: EventDetailViewProps) {
  const insets = useSafeAreaInsets();
  const [event, setEvent] = React.useState<EventDetail | null>(null);
  const [contactMap, setContactMap] = React.useState<Map<string, string>>(new Map());
  const [availableContacts, setAvailableContacts] = React.useState<Contact[]>([]);
  const [availablePlaces, setAvailablePlaces] = React.useState<EventPlaceOption[]>([]);
  const [isEditing, setIsEditing] = React.useState(editable);
  const [isSaving, setIsSaving] = React.useState(false);

  React.useEffect(() => {
    setIsEditing(editable);
  }, [editable]);

  React.useEffect(() => {
    let mounted = true;
    if (!eventId) {
      setEvent(null);
      return () => undefined;
    }

    (async () => {
      try {
        const result = (await apiFetch(`/mobile/events/${encodeURIComponent(eventId)}`)) as EventDetail;
        if (mounted) {
          setEvent(result);
        }
      } catch {
        if (mounted) {
          setEvent(null);
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, [eventId]);

  React.useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        if (!mounted) return;
        setAvailableContacts(result.contacts || []);
        const map = new Map<string, string>();
        for (const contact of result.contacts || []) {
          map.set(contact.contact_id, contact.display_name);
        }
        setContactMap(map);
      } catch {
        if (mounted) {
          setContactMap(new Map());
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, []);

  React.useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const result = (await apiFetch('/mobile/places?limit=500')) as PlaceListResponse;
        if (mounted) {
          setAvailablePlaces(result.places || []);
        }
      } catch {
        if (mounted) {
          setAvailablePlaces([]);
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, []);

  const draft = React.useMemo(() => {
    if (!event) return null;
    return toDraft(event, contactMap);
  }, [contactMap, event]);

  const title = String(event?.title || '').trim() || 'Event details';
  const subtitle = formatDateRange(event?.start_date, event?.end_date);

  const handleSave = React.useCallback(
    async (nextDraft: EventDraft) => {
      if (!eventId || !event) return;
      const nextStartDate = nextDraft.when.trim() || String(event.start_date || '').trim();
      const parsedStart = new Date(nextStartDate);
      if (!nextStartDate || Number.isNaN(parsedStart.getTime())) {
        Alert.alert('Date required', 'Select a valid date and time before saving this event.');
        return;
      }

      setIsSaving(true);
      try {
        await apiFetch('/mobile/ingest/event', {
          method: 'POST',
          body: JSON.stringify({
            id: eventId,
            startDate: parsedStart.toISOString(),
            endDate: event.end_date || null,
            placeId: nextDraft.placeId || null,
            people: nextDraft.participants.map((participant) => participant.contactId),
            tags: nextDraft.tags,
            types: nextDraft.types,
            title: nextDraft.title,
            summary: nextDraft.summary,
            raw: {},
            externalId: event.external_id || null,
          }),
        });

        const refreshed = (await apiFetch(`/mobile/events/${encodeURIComponent(eventId)}`)) as EventDetail;
        setEvent(refreshed);
        setIsEditing(false);
      } catch (error) {
        console.warn('[events] save failed', error);
        Alert.alert('Save failed', 'Unable to save this event right now.');
      } finally {
        setIsSaving(false);
      }
    },
    [event, eventId],
  );

  const content = (
    <EventDetailsForm
      initialDraft={draft || {
        title: '',
        summary: '',
        when: '',
        where: '',
        placeId: null,
        tags: [],
        types: [],
        participants: [],
      }}
      availableContacts={availableContacts}
      availablePlaces={availablePlaces}
      editable={isEditing}
      headerKicker={isEditing ? 'Event editor' : 'Linked event'}
      headerTitle={title}
      headerSubtitle={subtitle}
      doneLabel={isSaving ? 'Saving...' : 'Save changes'}
      onDone={isEditing && !isSaving ? handleSave : undefined}
    />
  );

  if (isEditing) {
    return content;
  }

  return (
    <>
      {content}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Edit event"
        onPress={() => setIsEditing(true)}
        style={({ pressed }) => [
          styles.fab,
          { bottom: insets.bottom + 20 },
          pressed && styles.fabPressed,
        ]}
      >
        <Ionicons name="create-outline" size={22} color="#fff" />
      </Pressable>
    </>
  );
}

const styles = StyleSheet.create({
  fab: {
    position: 'absolute',
    right: 20,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: '#101214',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.3,
    shadowRadius: 14,
    elevation: 10,
  },
  fabPressed: {
    transform: [{ scale: 0.97 }],
    opacity: 0.92,
  },
});
