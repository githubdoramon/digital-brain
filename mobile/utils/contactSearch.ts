import { normalizeSearch } from '@/utils/text';

type ContactSearchable = {
  display_name?: string | null;
  aliases?: string[] | null;
};

export function buildContactSearchText(contact: ContactSearchable): string {
  return [contact.display_name || '', ...(contact.aliases || [])].join(' ').trim();
}

export function matchesContactSearch(contact: ContactSearchable, query: string): boolean {
  const normalizedQuery = normalizeSearch(query.trim());
  if (!normalizedQuery) return true;
  return normalizeSearch(buildContactSearchText(contact)).includes(normalizedQuery);
}
