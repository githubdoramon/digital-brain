import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { type ContactGroup, matchesContactGroupSearch } from '@/utils/contactGroups';
import { matchesContactSearch } from '@/utils/contactSearch';
import { normalizeSearch } from '@/utils/text';
import { theme } from '@/theme';

export type ContactSearchEntry = {
  contact_id: string;
  display_name: string;
  aliases?: string[] | null;
};

type SearchResult<T extends ContactSearchEntry> =
  | { kind: 'contact'; label: string; contact: T }
  | { kind: 'group'; label: string; group: ContactGroup };

type ContactSearchSuggestionsProps<T extends ContactSearchEntry> = {
  query: string;
  contacts: T[];
  selectedContactIds: ReadonlySet<string>;
  groups?: ContactGroup[];
  includeGroups?: boolean;
  maxResults?: number;
  onSelectContact: (contact: T) => void;
  onSelectGroup?: (group: ContactGroup) => void;
};

export function ContactSearchSuggestions<T extends ContactSearchEntry>({
  query,
  contacts,
  selectedContactIds,
  groups = [],
  includeGroups = false,
  maxResults = 6,
  onSelectContact,
  onSelectGroup,
}: ContactSearchSuggestionsProps<T>) {
  const results = React.useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return [] as SearchResult<T>[];

    const matches: SearchResult<T>[] = contacts
      .filter((contact) => !selectedContactIds.has(contact.contact_id))
      .filter((contact) => matchesContactSearch(contact, trimmed))
      .map((contact) => ({ kind: 'contact', label: contact.display_name, contact }));

    if (includeGroups) {
      groups
        .filter((group) =>
          group.members.some((member) => !selectedContactIds.has(member.contact_id)),
        )
        .filter((group) => matchesContactGroupSearch(group, trimmed))
        .forEach((group) => matches.push({ kind: 'group', label: group.name, group }));
    }

    return matches
      .sort((left, right) =>
        normalizeSearch(left.label).localeCompare(normalizeSearch(right.label)),
      )
      .slice(0, maxResults);
  }, [contacts, groups, includeGroups, maxResults, query, selectedContactIds]);

  if (results.length === 0) return null;

  return (
    <View style={styles.list}>
      {results.map((result) => (
        <Pressable
          key={
            result.kind === 'contact'
              ? `contact:${result.contact.contact_id}`
              : `group:${result.group.group_id}`
          }
          accessibilityRole="button"
          accessibilityLabel={
            result.kind === 'contact'
              ? `Add ${result.contact.display_name}`
              : `Add ${result.group.name}, ${result.group.member_count} people`
          }
          onPress={() => {
            if (result.kind === 'contact') onSelectContact(result.contact);
            else onSelectGroup?.(result.group);
          }}
          style={({ pressed }) => [styles.row, pressed && styles.pressed]}
        >
          <View style={styles.copy}>
            <Text numberOfLines={1} style={styles.name}>
              {result.label}
            </Text>
            {result.kind === 'group' ? (
              <Text style={styles.subtitle}>
                {result.group.member_count} {result.group.member_count === 1 ? 'person' : 'people'}
              </Text>
            ) : null}
          </View>
          {result.kind === 'group' ? (
            <Ionicons name="people-outline" size={19} color={theme.colors.accentDeep} />
          ) : (
            <Ionicons name="add" size={18} color={theme.colors.accentDeep} />
          )}
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  list: {
    marginTop: 8,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.lg,
    backgroundColor: '#fff',
  },
  row: {
    minHeight: 50,
    paddingHorizontal: 14,
    paddingVertical: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.line,
  },
  pressed: {
    backgroundColor: theme.colors.paleTeal,
  },
  copy: {
    flex: 1,
    gap: 2,
  },
  name: {
    color: theme.colors.ink,
    fontSize: 15,
    fontWeight: '600',
  },
  subtitle: {
    color: theme.colors.mutedInk,
    fontSize: 12,
  },
});
