import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { ContactPicker, type ContactPickerEntry } from '@/components/ContactPicker';
import { type ContactGroup, type ContactGroupMember } from '@/utils/contactGroups';
import { theme } from '@/theme';

type Contact = ContactPickerEntry & { emails?: string[]; phones?: string[] };

function normalizeRouteParam(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? '';
}

export default function ContactGroupEditorScreen() {
  const params = useLocalSearchParams<{ groupId?: string | string[] }>();
  const groupId = normalizeRouteParam(params.groupId);
  const isCreating = !groupId || groupId === 'new';
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [name, setName] = React.useState('');
  const [query, setQuery] = React.useState('');
  const [contacts, setContacts] = React.useState<Contact[]>([]);
  const [members, setMembers] = React.useState<ContactGroupMember[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let mounted = true;
    void Promise.all([
      apiFetch('/mobile/contacts') as Promise<{ contacts?: Contact[] }>,
      isCreating
        ? Promise.resolve(null)
        : (apiFetch(
            `/mobile/contact-groups/${encodeURIComponent(groupId)}`,
          ) as Promise<ContactGroup>),
    ])
      .then(([contactsResult, group]) => {
        if (!mounted) return;
        setContacts(contactsResult.contacts ?? []);
        if (group) {
          setName(group.name);
          setMembers(group.members ?? []);
        }
      })
      .catch(() => {
        if (mounted) setError('Unable to load this group.');
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [groupId, isCreating]);

  const memberIds = React.useMemo(
    () => new Set(members.map((member) => member.contact_id)),
    [members],
  );
  const handleAddMember = React.useCallback((contact: Contact) => {
    setMembers((previous) => {
      if (previous.some((member) => member.contact_id === contact.contact_id)) return previous;
      return [...previous, { contact_id: contact.contact_id, display_name: contact.display_name }];
    });
    setQuery('');
  }, []);

  const handleSave = React.useCallback(async () => {
    const cleanName = name.trim();
    if (!cleanName) {
      setError('Add a name for this group.');
      return;
    }
    if (members.length === 0) {
      setError('Add at least one person to this group.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: cleanName,
        member_contact_ids: members.map((member) => member.contact_id),
      };
      if (isCreating) {
        await apiFetch('/mobile/contact-groups', { method: 'POST', body: JSON.stringify(payload) });
      } else {
        await apiFetch(`/mobile/contact-groups/${encodeURIComponent(groupId)}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
      }
      router.back();
    } catch {
      setError('Unable to save this group. Try again.');
    } finally {
      setSaving(false);
    }
  }, [groupId, isCreating, members, name, router]);

  const handleDelete = React.useCallback(() => {
    Alert.alert(
      'Delete group?',
      `“${name || 'This group'}” will be deleted. Its contacts will stay in your contacts list.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete group',
          style: 'destructive',
          onPress: () => {
            setDeleting(true);
            void apiFetch(`/mobile/contact-groups/${encodeURIComponent(groupId)}`, {
              method: 'DELETE',
            })
              .then(() => router.back())
              .catch(() => {
                setDeleting(false);
                Alert.alert('Could not delete group', 'Please try again.');
              });
          },
        },
      ],
    );
  }, [groupId, name, router]);

  return (
    <View style={styles.screen}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Go back"
          onPress={() => router.back()}
          style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}
        >
          <Ionicons name="arrow-back" size={22} color={theme.colors.ink} />
        </Pressable>
        <Text style={styles.headerTitle}>{isCreating ? 'Create a group' : 'Edit group'}</Text>
        <View style={styles.headerSpacer} />
      </View>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView
          contentContainerStyle={[
            styles.content,
            {
              paddingTop: 12,
              paddingBottom: insets.bottom + 116,
            },
          ]}
          keyboardShouldPersistTaps="always"
          keyboardDismissMode="none"
          automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'}
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.intro}>
            <View style={styles.groupMark}>
              <Ionicons name="people" size={22} color={theme.colors.accentDeep} />
            </View>
            <Text style={styles.subtitle}>Keep the people you often choose together.</Text>
          </View>

          {loading ? (
            <ActivityIndicator color={theme.colors.accentDeep} style={styles.loader} />
          ) : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}

          <Card style={styles.card}>
            <Text style={styles.label}>Group name</Text>
            <TextInput
              value={name}
              onChangeText={(value) => {
                setName(value);
                setError(null);
              }}
              placeholder="e.g. Book club"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.input}
              returnKeyType="done"
              maxLength={80}
            />

            <View style={styles.sectionHeading}>
              <View>
                <Text style={styles.label}>People</Text>
                <Text style={styles.helper}>
                  {members.length} {members.length === 1 ? 'person' : 'people'}
                </Text>
              </View>
              <Ionicons name="person-add-outline" size={19} color={theme.colors.accentDeep} />
            </View>

            {members.length > 0 ? (
              <View style={styles.memberList}>
                {members.map((member) => (
                  <View key={member.contact_id} style={styles.memberRow}>
                    <View style={styles.memberAvatar}>
                      <Text style={styles.memberInitial}>
                        {member.display_name.trim().slice(0, 1).toUpperCase() || '?'}
                      </Text>
                    </View>
                    <Text numberOfLines={1} style={styles.memberName}>
                      {member.display_name}
                    </Text>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`Remove ${member.display_name}`}
                      onPress={() =>
                        setMembers((current) =>
                          current.filter((item) => item.contact_id !== member.contact_id),
                        )
                      }
                      style={({ pressed }) => [styles.removeButton, pressed && styles.pressed]}
                    >
                      <Ionicons name="close" size={17} color={theme.colors.mutedInk} />
                    </Pressable>
                  </View>
                ))}
              </View>
            ) : (
              <View style={styles.emptyMembers}>
                <Text style={styles.emptyTitle}>Add people to this group</Text>
                <Text style={styles.helper}>Search your contacts below.</Text>
              </View>
            )}

            <Text style={[styles.label, styles.searchLabel]}>Add people</Text>
            <ContactPicker
              query={query}
              onQueryChange={(value) => {
                setQuery(value);
                setError(null);
              }}
              contacts={contacts.filter((contact) => !memberIds.has(contact.contact_id))}
              selectedContactIds={memberIds}
              placeholder="Search contacts"
              leadingIcon
              inputContainerStyle={styles.searchWrap}
              inputStyle={styles.searchInput}
              onSelectContact={handleAddMember}
            />
          </Card>

          {!loading ? (
            <Pressable
              accessibilityRole="button"
              onPress={() => void handleSave()}
              disabled={saving || deleting}
              style={({ pressed }) => [
                styles.saveButton,
                (saving || deleting) && styles.disabled,
                pressed && styles.pressed,
              ]}
            >
              {saving ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Ionicons name="checkmark" size={20} color="#fff" />
              )}
              <Text style={styles.saveText}>{isCreating ? 'Create group' : 'Save changes'}</Text>
            </Pressable>
          ) : null}

          {!isCreating ? (
            <Pressable
              accessibilityRole="button"
              onPress={handleDelete}
              disabled={saving || deleting}
              style={({ pressed }) => [styles.deleteButton, pressed && styles.pressed]}
            >
              {deleting ? (
                <ActivityIndicator color={theme.colors.accent} />
              ) : (
                <Ionicons name="trash-outline" size={17} color={theme.colors.accent} />
              )}
              <Text style={styles.deleteText}>Delete group</Text>
            </Pressable>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 18,
    paddingBottom: 8,
    gap: 12,
  },
  backButton: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 20,
  },
  headerTitle: { color: theme.colors.ink, fontSize: 21, fontWeight: '700', letterSpacing: -0.3 },
  headerSpacer: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 14 },
  intro: { alignItems: 'center', paddingBottom: 6 },
  groupMark: {
    width: 52,
    height: 52,
    borderRadius: 18,
    backgroundColor: theme.colors.paleTeal,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  subtitle: { color: theme.colors.mutedInk, fontSize: 14, marginTop: 2, textAlign: 'center' },
  loader: { marginVertical: 16 },
  error: { color: theme.colors.accent, fontSize: 13, fontWeight: '600' },
  card: { padding: 18, gap: 10 },
  label: { color: theme.colors.ink, fontSize: 14, fontWeight: '700' },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: theme.colors.ink,
    fontSize: 16,
    backgroundColor: '#fff',
  },
  sectionHeading: {
    marginTop: 16,
    paddingTop: 16,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.line,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  helper: { color: theme.colors.mutedInk, fontSize: 12 },
  memberList: { gap: 4, marginTop: 2 },
  memberRow: {
    minHeight: 50,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    paddingVertical: 5,
  },
  memberAvatar: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: theme.colors.paleTeal,
    alignItems: 'center',
    justifyContent: 'center',
  },
  memberInitial: { color: theme.colors.teal, fontSize: 13, fontWeight: '700' },
  memberName: { flex: 1, color: theme.colors.ink, fontSize: 14, fontWeight: '600' },
  removeButton: {
    width: 34,
    height: 34,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 17,
  },
  emptyMembers: {
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: theme.colors.line,
    backgroundColor: '#fbfaf8',
    borderRadius: theme.radius.md,
    padding: 16,
    marginTop: 4,
  },
  emptyTitle: { color: theme.colors.ink, fontSize: 14, fontWeight: '600', marginBottom: 3 },
  searchLabel: { marginTop: 10 },
  searchWrap: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 13,
  },
  searchInput: { flex: 1, color: theme.colors.ink, fontSize: 15, paddingVertical: 10 },
  suggestions: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    overflow: 'hidden',
    marginTop: 2,
  },
  suggestion: {
    minHeight: 58,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    paddingHorizontal: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.line,
  },
  saveButton: {
    minHeight: 54,
    borderRadius: 18,
    backgroundColor: theme.colors.accentDeep,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 9,
    marginTop: 2,
    shadowColor: '#1d2927',
    shadowOpacity: 0.2,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 5,
  },
  saveText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  disabled: { opacity: 0.55 },
  deleteButton: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 2,
    marginBottom: 8,
  },
  deleteText: { color: theme.colors.accent, fontSize: 14, fontWeight: '600' },
  pressed: { opacity: 0.76 },
});
