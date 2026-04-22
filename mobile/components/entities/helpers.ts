import type { EntityFilterOption, EntityFilters, EventListItem, PlaceListItem } from './types';

export function formatEventDate(value?: string | null): string {
  if (!value) return 'Date TBD';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function formatPlaceSubtitle(place: PlaceListItem): string {
  const description = (place.description || '').trim();
  if (description) {
    return description;
  }
  const pieces = [place.address, place.city, place.country]
    .map((value) => (value || '').trim())
    .filter(Boolean);
  if (pieces.length > 0) return pieces.join(' • ');
  if (typeof place.lat === 'number' && typeof place.lon === 'number') {
    return `${place.lat.toFixed(4)}, ${place.lon.toFixed(4)}`;
  }
  return 'No location details yet';
}

export function formatEventFilterDescription(event: EventListItem): string {
  if (event.summary?.trim()) {
    return event.summary.trim();
  }
  return formatEventDate(event.start_date);
}

export function buildFilterOptionMaps(options: EntityFilterOption[]): Map<string, EntityFilterOption> {
  return new Map(options.map((option) => [`${option.kind}:${option.id}`, option]));
}

export function buildActiveFilterChips(
  filters: EntityFilters,
  optionMap: Map<string, EntityFilterOption>,
): { id: string; kind: EntityFilterOption['kind']; label: string }[] {
  const entries: { id: string; kind: EntityFilterOption['kind']; label: string }[] = [];
  for (const contactId of filters.contactIds) {
    const option = optionMap.get(`contacts:${contactId}`);
    if (option) entries.push({ id: contactId, kind: 'contacts', label: option.label });
  }
  for (const placeId of filters.placeIds) {
    const option = optionMap.get(`places:${placeId}`);
    if (option) entries.push({ id: placeId, kind: 'places', label: option.label });
  }
  for (const eventId of filters.eventIds) {
    const option = optionMap.get(`events:${eventId}`);
    if (option) entries.push({ id: eventId, kind: 'events', label: option.label });
  }
  return entries;
}

export function countActiveFilters(filters: EntityFilters): number {
  return filters.contactIds.length + filters.placeIds.length + filters.eventIds.length;
}
