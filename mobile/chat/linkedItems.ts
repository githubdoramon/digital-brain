export type LinkedItem = {
  entity_type: 'event' | 'document';
  entity_id: string;
  title: string;
  subtitle?: string | null;
};

export function routeForLinkedItem(item: LinkedItem): string | null {
  const entityId = item.entity_id.trim();
  if (!entityId) return null;

  if (item.entity_type === 'event') {
    return `/events/${encodeURIComponent(entityId)}`;
  }
  if (item.entity_type === 'document') {
    return `/documents/${encodeURIComponent(entityId)}`;
  }
  return null;
}
