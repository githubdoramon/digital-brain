import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import {
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
  ViewStyle,
} from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { type ContactGroup, matchesContactGroupSearch } from '@/utils/contactGroups';
import { matchesContactSearch } from '@/utils/contactSearch';
import { normalizeSearch } from '@/utils/text';
import { theme } from '@/theme';

export type ContactPickerEntry = {
  contact_id: string;
  display_name: string;
  aliases?: string[] | null;
};

type Result<T extends ContactPickerEntry> =
  | { kind: 'contact'; label: string; contact: T }
  | { kind: 'group'; label: string; group: ContactGroup };

type ContactPickerProps<T extends ContactPickerEntry> = {
  query: string;
  onQueryChange: (query: string) => void;
  contacts: T[];
  selectedContactIds?: ReadonlySet<string>;
  groups?: ContactGroup[];
  includeGroups?: boolean;
  maxResults?: number;
  placeholder?: string;
  inputStyle?: TextInputProps['style'];
  inputContainerStyle?: StyleProp<ViewStyle>;
  leadingIcon?: boolean;
  returnKeyType?: TextInputProps['returnKeyType'];
  onSubmitEditing?: TextInputProps['onSubmitEditing'];
  onSelectContact: (contact: T) => void;
  onSelectGroup?: (group: ContactGroup) => void;
};

export function ContactPicker<T extends ContactPickerEntry>({
  query,
  onQueryChange,
  contacts,
  selectedContactIds = new Set<string>(),
  groups = [],
  includeGroups = false,
  maxResults = 6,
  placeholder = 'Search contacts',
  inputStyle,
  inputContainerStyle,
  leadingIcon = false,
  returnKeyType = 'search',
  onSubmitEditing,
  onSelectContact,
  onSelectGroup,
}: ContactPickerProps<T>) {
  const results = React.useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return [] as Result<T>[];

    const matches: Result<T>[] = contacts
      .filter((contact) => !selectedContactIds.has(contact.contact_id))
      .filter((contact) => matchesContactSearch(contact, trimmed))
      .map((contact) => ({ kind: 'contact', label: contact.display_name, contact }));

    if (includeGroups) {
      groups
        .filter((group) => matchesContactGroupSearch(group, trimmed))
        .forEach((group) => matches.push({ kind: 'group', label: group.name, group }));
    }

    return matches
      .sort((left, right) => {
        if (includeGroups && left.kind !== right.kind) return left.kind === 'group' ? -1 : 1;
        return normalizeSearch(left.label).localeCompare(normalizeSearch(right.label));
      })
      .slice(0, maxResults);
  }, [contacts, groups, includeGroups, maxResults, query, selectedContactIds]);

  return (
    <View>
      <View
        style={[
          styles.inputContainer,
          leadingIcon ? styles.searchContainer : null,
          inputContainerStyle,
        ]}
      >
        {leadingIcon ? (
          <Ionicons name="search-outline" size={18} color={theme.colors.mutedInk} />
        ) : null}
        <TextInput
          value={query}
          onChangeText={onQueryChange}
          placeholder={placeholder}
          placeholderTextColor={theme.colors.mutedInk}
          style={[leadingIcon ? styles.inlineInput : styles.standaloneInput, inputStyle]}
          blurOnSubmit={false}
          returnKeyType={returnKeyType}
          onSubmitEditing={onSubmitEditing}
        />
        {query ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Clear contact search"
            onPress={() => onQueryChange('')}
            hitSlop={8}
            style={leadingIcon ? undefined : styles.clearOverlay}
          >
            <Ionicons name="close-circle" size={18} color={theme.colors.mutedInk} />
          </Pressable>
        ) : null}
      </View>
      {results.length > 0 ? (
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
              style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
            >
              <View style={styles.avatar}>
                <Text style={styles.avatarInitial}>
                  {result.label.trim().slice(0, 1).toUpperCase() || '?'}
                </Text>
              </View>
              <View style={styles.copy}>
                <Text numberOfLines={1} style={styles.name}>
                  {result.label}
                </Text>
                {result.kind === 'group' ? (
                  <Text style={styles.subtitle}>
                    {result.group.member_count}{' '}
                    {result.group.member_count === 1 ? 'person' : 'people'}
                  </Text>
                ) : result.contact.aliases?.length ? (
                  <Text numberOfLines={1} style={styles.subtitle}>
                    {result.contact.aliases.join(', ')}
                  </Text>
                ) : null}
              </View>
              <Ionicons
                name={result.kind === 'group' ? 'people-outline' : 'add-circle-outline'}
                size={21}
                color={theme.colors.accentDeep}
              />
            </Pressable>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  searchContainer: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 13,
  },
  inlineInput: { flex: 1, minWidth: 0, color: theme.colors.ink, fontSize: 15, paddingVertical: 10 },
  standaloneInput: {
    flex: 1,
    minWidth: 0,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    color: theme.colors.ink,
    fontSize: 15,
    paddingHorizontal: 13,
    paddingRight: 40,
    paddingVertical: 11,
  },
  clearOverlay: { position: 'absolute', right: 13, top: 0, bottom: 0, justifyContent: 'center' },
  list: {
    marginTop: 8,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
  },
  row: {
    minHeight: 64,
    paddingHorizontal: 12,
    paddingVertical: 9,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.line,
  },
  rowPressed: { backgroundColor: theme.colors.paleTeal },
  avatar: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: theme.colors.paleTeal,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInitial: { color: theme.colors.teal, fontSize: 14, fontWeight: '700' },
  copy: { flex: 1, minWidth: 0, justifyContent: 'center', gap: 3 },
  name: { color: theme.colors.ink, fontSize: 15, lineHeight: 20, fontWeight: '600' },
  subtitle: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 16 },
});
