import { useLocalSearchParams } from 'expo-router';
import React from 'react';

import { apiFetch } from '@/api/client';
import {
  EventDetailsForm,
  EventDraftEditorScreen,
} from '@/components/event-draft/EventDraftEditorScreen';
import type { EventDraft } from '@/components/event-draft/types';

type EventDetail = {
  id: string;
  title?: string | null;
  summary?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  tags?: string[] | null;
  people?: string[] | null;
  place?: {
    place_id: string;
    name?: string | null;
    city?: string | null;
    country?: string | null;
  } | null;
  types?: string[] | null;
};

type Contact = {
  contact_id: string;
  display_name: string;
};

type RouteParams = {
  eventId: string;
  draftSessionId?: string;
};

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
    tags: Array.isArray(event.tags) ? event.tags.map((tag) => String(tag || '').trim()).filter(Boolean) : [],
    types: Array.isArray(event.types) ? event.types.map((typeValue) => String(typeValue || '').trim()).filter(Boolean) : [],
    participants: (event.people || []).map((id) => ({
      contactId: id,
      displayName: contactMap.get(id) || id,
    })),
  };
}

export default function EventScreenRoute() {
  const params = useLocalSearchParams<RouteParams>();
  const eventId = Array.isArray(params.eventId) ? params.eventId[0] : params.eventId;
  const draftSessionId = Array.isArray(params.draftSessionId)
    ? params.draftSessionId[0]
    : params.draftSessionId;

  React.useEffect(() => {
    console.info('[event-draft-session] event-route-params', {
      eventId,
      draftSessionId: draftSessionId ?? null,
    });
  }, [draftSessionId, eventId]);

  if (draftSessionId) {
    return <EventDraftEditorScreen sessionId={draftSessionId} />;
  }

  return <EventDetailView eventId={eventId} />;
}

type EventDetailViewProps = {
  eventId?: string;
};

function EventDetailView({ eventId }: EventDetailViewProps) {
  const [event, setEvent] = React.useState<EventDetail | null>(null);
  const [contactMap, setContactMap] = React.useState<Map<string, string>>(new Map());

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

  const draft = React.useMemo(() => {
    if (!event) return null;
    return toDraft(event, contactMap);
  }, [contactMap, event]);

  const title = String(event?.title || '').trim() || 'Event details';
  const subtitle = formatDateRange(event?.start_date, event?.end_date);

  return (
    <EventDetailsForm
      initialDraft={draft || {
        title: '',
        summary: '',
        when: '',
        where: '',
        tags: [],
        types: [],
        participants: [],
      }}
      availableContacts={[]}
      editable={false}
      headerKicker="Linked event"
      headerTitle={title}
      headerSubtitle={subtitle}
    />
  );
}
