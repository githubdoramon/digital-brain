import { apiFetch } from '@/api/client';
import { normalizeSearch } from '@/utils/text';

export type ContactGroupMember = {
  contact_id: string;
  display_name: string;
};

export type ContactGroup = {
  group_id: string;
  name: string;
  members: ContactGroupMember[];
  member_count: number;
  aliases?: string[];
  status?: string;
};

export async function fetchContactGroups(): Promise<ContactGroup[]> {
  const response = (await apiFetch('/mobile/contact-groups')) as { groups?: ContactGroup[] };
  return response.groups ?? [];
}

export function matchesContactGroupSearch(group: ContactGroup, query: string): boolean {
  const normalizedQuery = normalizeSearch(query.trim());
  if (!normalizedQuery) return true;
  return normalizeSearch([group.name, ...(group.aliases ?? [])].join(' ')).includes(
    normalizedQuery,
  );
}
