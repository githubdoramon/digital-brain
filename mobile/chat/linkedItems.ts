import type { Href } from 'expo-router';

export type LinkedItem = {
  entity_type: 'event' | 'document';
  entity_id: string;
  title: string;
  subtitle?: string | null;
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
  return null;
}
