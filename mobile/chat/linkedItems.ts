import type { Href } from 'expo-router';

export type LinkedItemType = 'event' | 'document' | 'contact' | 'place';

export type LinkedItem = {
  entity_type: LinkedItemType;
  entity_id: string;
  title: string;
  subtitle?: string | null;
  role?: string | null;
};

export function routeForLinkedItem(item: LinkedItem): Href | null {
  const entityId = item.entity_id.trim();
  if (!entityId) return null;

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
  return routeForLinkedItem(item) !== null;
}
