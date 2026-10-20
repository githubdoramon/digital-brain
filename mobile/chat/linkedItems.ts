import type { Href } from 'expo-router';

export type LinkedItemType =
  | 'event'
  | 'document'
  | 'contact'
  | 'place'
  | 'conversation'
  | 'message';

export type LinkedItem = {
  entity_type: LinkedItemType;
  entity_id: string;
  title: string;
  subtitle?: string | null;
  role?: string | null;
  message_id?: number | null;
  source_url?: string | null;
};

export function routeForLinkedItem(item: LinkedItem): Href | null {
  const entityId = item.entity_id.trim();
  if (!entityId) return null;
  if (item.entity_type === 'conversation') {
    return {
      pathname: '/chat/[threadId]',
      params: {
        threadId: entityId,
        ...(item.message_id ? { messageId: String(item.message_id) } : {}),
      },
    };
  }

  if (item.entity_type === 'event') {
    return { pathname: '/events/[eventId]', params: { eventId: entityId } };
  }
  if (item.entity_type === 'document') {
    return { pathname: '/documents/[documentId]', params: { documentId: entityId } };
  }
  if (item.entity_type === 'contact') {
    return { pathname: '/contacts/[contactId]', params: { contactId: entityId } };
  }
  if (item.entity_type === 'place') {
    return { pathname: '/places/[placeId]', params: { placeId: entityId } };
  }
  return null;
}

export function isLinkedItemNavigable(item: LinkedItem): boolean {
  if (item.entity_type === 'message') return !!externalMessageUrl(item);
  return routeForLinkedItem(item) !== null;
}

export function externalMessageUrl(item: LinkedItem): string | null {
  return item.entity_type === 'message' &&
    item.source_url &&
    /^https:\/\/(?:wa\.me|t\.me)\//.test(item.source_url)
    ? item.source_url
    : null;
}
