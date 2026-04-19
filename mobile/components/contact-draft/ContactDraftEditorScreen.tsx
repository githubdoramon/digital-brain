import { useRouter } from 'expo-router';
import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import {
  clearContactDraftEditSession,
  getContactDraftEditSession,
  submitContactDraftEditSession,
} from '@/contact-draft/draftEditorSession';
import type {
  ContactDraftContact,
  ContactDraftPlace,
  ContactDraftPlaceLink,
  ContactDraftRelationship,
  ContactProposalDraft,
} from '@/contact-draft/types';
import type { EventContactOption, EventPlaceOption } from '@/components/event-draft/types';
import { theme } from '@/theme';
import { matchesContactSearch } from '@/utils/contactSearch';
import { normalizeSearch } from '@/utils/text';

type Props = {
  sessionId?: string | null;
};

function updateContact(
  draft: ContactProposalDraft,
  proposalId: string,
  updater: (contact: ContactDraftContact) => ContactDraftContact,
) {
  return {
    ...draft,
    contacts: draft.contacts.map((contact) =>
      contact.proposalId === proposalId ? updater(contact) : contact,
    ),
  };
}

function updateRelationship(
  draft: ContactProposalDraft,
  proposalId: string,
  updater: (relationship: ContactDraftRelationship) => ContactDraftRelationship,
) {
  return {
    ...draft,
    relationships: draft.relationships.map((relationship) =>
      relationship.proposalId === proposalId ? updater(relationship) : relationship,
    ),
  };
}

function updatePlace(
  draft: ContactProposalDraft,
  proposalId: string,
  updater: (place: ContactDraftPlace) => ContactDraftPlace,
) {
  return {
    ...draft,
    places: draft.places.map((place) => (place.proposalId === proposalId ? updater(place) : place)),
  };
}

function updatePlaceLink(
  draft: ContactProposalDraft,
  proposalId: string,
  updater: (link: ContactDraftPlaceLink) => ContactDraftPlaceLink,
) {
  return {
    ...draft,
    placeLinks: draft.placeLinks.map((link) =>
      link.proposalId === proposalId ? updater(link) : link,
    ),
  };
}

function buildPlaceSearchText(place: EventPlaceOption): string {
  return [place.name, place.address, place.city, place.country, ...(place.aliases || [])]
    .filter(Boolean)
    .join(' ');
}

function ContactSuggestions({
  query,
  contacts,
  onSelect,
}: {
  query: string;
  contacts: EventContactOption[];
  onSelect: (contact: EventContactOption) => void;
}) {
  const filtered = React.useMemo(() => {
    const needle = query.trim();
    if (!needle) return [];
    return contacts.filter((contact) => matchesContactSearch(contact, needle)).slice(0, 5);
  }, [contacts, query]);
  if (filtered.length === 0) return null;
  return (
    <View style={styles.suggestionList}>
      {filtered.map((contact) => (
        <Pressable key={contact.contact_id} onPress={() => onSelect(contact)} style={({ pressed }) => [styles.suggestionItem, pressed && styles.pressed]}>
          <Text style={styles.suggestionTitle}>{contact.display_name}</Text>
          {contact.aliases?.length ? <Text style={styles.suggestionMeta}>{contact.aliases.join(', ')}</Text> : null}
        </Pressable>
      ))}
    </View>
  );
}

function PlaceSuggestions({
  query,
  places,
  onSelect,
}: {
  query: string;
  places: EventPlaceOption[];
  onSelect: (place: EventPlaceOption) => void;
}) {
  const filtered = React.useMemo(() => {
    const needle = normalizeSearch(query.trim());
    if (!needle) return [];
    return places.filter((place) => normalizeSearch(buildPlaceSearchText(place)).includes(needle)).slice(0, 5);
  }, [places, query]);
  if (filtered.length === 0) return null;
  return (
    <View style={styles.suggestionList}>
      {filtered.map((place) => (
        <Pressable key={place.place_id} onPress={() => onSelect(place)} style={({ pressed }) => [styles.suggestionItem, pressed && styles.pressed]}>
          <Text style={styles.suggestionTitle}>{place.name || place.place_id}</Text>
          <Text style={styles.suggestionMeta}>
            {[place.address, place.city, place.country].filter(Boolean).join(', ')}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

export function ContactDraftEditorScreen({ sessionId }: Props) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const session = React.useMemo(() => getContactDraftEditSession(sessionId), [sessionId]);
  const [draft, setDraft] = React.useState<ContactProposalDraft | null>(session?.initialDraft ?? null);

  React.useEffect(() => {
    setDraft(session?.initialDraft ?? null);
  }, [session]);

  const handleSave = React.useCallback(() => {
    if (!sessionId || !draft) return;
    submitContactDraftEditSession(sessionId, draft);
    router.back();
  }, [draft, router, sessionId]);

  const handleCancel = React.useCallback(() => {
    clearContactDraftEditSession(sessionId);
    router.back();
  }, [router, sessionId]);

  if (!session || !draft) {
    return (
      <View style={styles.emptyWrap}>
        <Text style={styles.emptyTitle}>Contact draft unavailable</Text>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={0}
    >
      <ScrollView
        contentContainerStyle={{
          paddingTop: insets.top + 20,
          paddingBottom: insets.bottom + 96,
          paddingHorizontal: 16,
          gap: 14,
        }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.header}>
          <Text style={styles.title}>Edit contact draft</Text>
          <Pressable onPress={handleCancel} style={({ pressed }) => [styles.cancelButton, pressed && styles.pressed]}>
            <Text style={styles.cancelText}>Cancel</Text>
          </Pressable>
        </View>

        {draft.contacts.map((contact) => (
          <Card key={contact.proposalId} style={styles.card}>
            <Text style={styles.cardTitle}>
              {contact.operation === 'create' ? 'New contact' : 'Contact update'}
            </Text>
            <TextInput
              style={styles.input}
              value={contact.displayName}
              onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, displayName: value })) : current)}
              placeholder="Display name"
              placeholderTextColor={theme.colors.mutedInk}
            />
            <ContactSuggestions
              query={contact.displayName}
              contacts={session.availableContacts}
              onSelect={(selected) =>
                setDraft((current) =>
                  current
                    ? updateContact(current, contact.proposalId, (item) => ({
                        ...item,
                        reference: selected.contact_id,
                        operation: 'update',
                        displayName: selected.display_name,
                      }))
                    : current,
                )
              }
            />
            <TextInput style={styles.input} value={contact.birthday} onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, birthday: value })) : current)} placeholder="Birthday YYYY-MM-DD" placeholderTextColor={theme.colors.mutedInk} />
            <TextInput style={styles.input} value={contact.aliasesText} onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, aliasesText: value })) : current)} placeholder="Aliases, comma separated" placeholderTextColor={theme.colors.mutedInk} />
            <TextInput style={styles.input} value={contact.emailsText} onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, emailsText: value })) : current)} placeholder="Emails, comma separated" placeholderTextColor={theme.colors.mutedInk} keyboardType="email-address" />
            <TextInput style={styles.input} value={contact.phonesText} onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, phonesText: value })) : current)} placeholder="Phones, comma separated" placeholderTextColor={theme.colors.mutedInk} />
            <TextInput style={styles.input} value={contact.linksText} onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, linksText: value })) : current)} placeholder="Links, comma separated" placeholderTextColor={theme.colors.mutedInk} keyboardType="url" />
            <TextInput style={styles.input} value={contact.tagsText} onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, tagsText: value })) : current)} placeholder="Tags, comma separated" placeholderTextColor={theme.colors.mutedInk} />
            <TextInput
              style={[styles.input, styles.textarea]}
              value={contact.comments}
              onChangeText={(value) => setDraft((current) => current ? updateContact(current, contact.proposalId, (item) => ({ ...item, comments: value })) : current)}
              placeholder="Notes"
              placeholderTextColor={theme.colors.mutedInk}
              multiline
            />
          </Card>
        ))}

        {draft.relationships.map((relationship) => (
          <Card key={relationship.proposalId} style={styles.card}>
            <Text style={styles.cardTitle}>
              {relationship.kind === 'derived' ? 'Inferred relationship' : 'Relationship'}
            </Text>
            <TextInput style={styles.input} value={relationship.fromDisplayName} onChangeText={(value) => setDraft((current) => current ? updateRelationship(current, relationship.proposalId, (item) => ({ ...item, fromDisplayName: value })) : current)} placeholder="From contact" placeholderTextColor={theme.colors.mutedInk} />
            <ContactSuggestions
              query={relationship.fromDisplayName}
              contacts={session.availableContacts}
              onSelect={(selected) =>
                setDraft((current) =>
                  current
                    ? updateRelationship(current, relationship.proposalId, (item) => ({
                        ...item,
                        fromReference: selected.contact_id,
                        fromDisplayName: selected.display_name,
                      }))
                    : current,
                )
              }
            />
            <TextInput style={styles.input} value={relationship.relationshipType} onChangeText={(value) => setDraft((current) => current ? updateRelationship(current, relationship.proposalId, (item) => ({ ...item, relationshipType: value })) : current)} placeholder="Relationship type" placeholderTextColor={theme.colors.mutedInk} />
            <TextInput style={styles.input} value={relationship.toDisplayName} onChangeText={(value) => setDraft((current) => current ? updateRelationship(current, relationship.proposalId, (item) => ({ ...item, toDisplayName: value })) : current)} placeholder="To contact" placeholderTextColor={theme.colors.mutedInk} />
            <ContactSuggestions
              query={relationship.toDisplayName}
              contacts={session.availableContacts}
              onSelect={(selected) =>
                setDraft((current) =>
                  current
                    ? updateRelationship(current, relationship.proposalId, (item) => ({
                        ...item,
                        toReference: selected.contact_id,
                        toDisplayName: selected.display_name,
                      }))
                    : current,
                )
              }
            />
            {relationship.kind === 'derived' ? (
              <View style={styles.switchRow}>
                <Text style={styles.switchLabel}>Apply inferred relationship</Text>
                <Switch
                  value={relationship.enabled}
                  onValueChange={(value) => setDraft((current) => current ? updateRelationship(current, relationship.proposalId, (item) => ({ ...item, enabled: value })) : current)}
                />
              </View>
            ) : null}
          </Card>
        ))}

        {draft.places.map((place) => (
          <Card key={place.proposalId} style={styles.card}>
            <Text style={styles.cardTitle}>Place</Text>
            <TextInput style={styles.input} value={place.name} onChangeText={(value) => setDraft((current) => current ? updatePlace(current, place.proposalId, (item) => ({ ...item, name: value })) : current)} placeholder="Place name" placeholderTextColor={theme.colors.mutedInk} />
            <PlaceSuggestions
              query={place.name}
              places={session.availablePlaces}
              onSelect={(selected) =>
                setDraft((current) =>
                  current
                    ? updatePlace(current, place.proposalId, (item) => ({
                        ...item,
                        reference: selected.place_id,
                        name: selected.name || selected.place_id,
                        address: [selected.address, selected.city, selected.country].filter(Boolean).join(', '),
                      }))
                    : current,
                )
              }
            />
            <TextInput style={styles.input} value={place.address} onChangeText={(value) => setDraft((current) => current ? updatePlace(current, place.proposalId, (item) => ({ ...item, address: value })) : current)} placeholder="Address" placeholderTextColor={theme.colors.mutedInk} />
          </Card>
        ))}

        {draft.placeLinks.map((link) => (
          <Card key={link.proposalId} style={styles.card}>
            <Text style={styles.cardTitle}>Place link</Text>
            <Text style={styles.metaText}>
              {link.contactDisplayName || 'Contact'} {'->'} {link.placeName || 'Place'}
            </Text>
            <ContactSuggestions
              query={link.contactDisplayName}
              contacts={session.availableContacts}
              onSelect={(selected) =>
                setDraft((current) =>
                  current
                    ? updatePlaceLink(current, link.proposalId, (item) => ({
                        ...item,
                        contactReference: selected.contact_id,
                        contactDisplayName: selected.display_name,
                      }))
                    : current,
                )
              }
            />
            <PlaceSuggestions
              query={link.placeName}
              places={session.availablePlaces}
              onSelect={(selected) =>
                setDraft((current) =>
                  current
                    ? {
                        ...updatePlaceLink(current, link.proposalId, (item) => ({
                          ...item,
                          placeReference: selected.place_id,
                          placeName: selected.name || selected.place_id,
                        })),
                        places: current.places.map((place) =>
                          place.reference === link.placeReference
                            ? {
                                ...place,
                                reference: selected.place_id,
                                name: selected.name || selected.place_id,
                                address: [selected.address, selected.city, selected.country]
                                  .filter(Boolean)
                                  .join(', '),
                              }
                            : place,
                        ),
                      }
                    : current,
                )
              }
            />
            <TextInput style={styles.input} value={link.role} onChangeText={(value) => setDraft((current) => current ? updatePlaceLink(current, link.proposalId, (item) => ({ ...item, role: value })) : current)} placeholder="Role" placeholderTextColor={theme.colors.mutedInk} />
          </Card>
        ))}
      </ScrollView>

      <FloatingSaveButton visible label="Apply draft edits" onPress={handleSave} bottomOffset={insets.bottom + 20} />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.colors.bg },
  emptyWrap: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: theme.colors.bg },
  emptyTitle: { color: theme.colors.ink, fontSize: 18, fontWeight: '700' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  title: { color: theme.colors.ink, fontSize: 28, fontWeight: '800' },
  cancelButton: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: theme.radius.md, backgroundColor: theme.colors.card, borderWidth: 1, borderColor: theme.colors.line },
  cancelText: { color: theme.colors.mutedInk, fontSize: 14, fontWeight: '600' },
  pressed: { opacity: 0.8 },
  card: { padding: 16, gap: 10 },
  cardTitle: { color: theme.colors.ink, fontSize: 16, fontWeight: '700' },
  metaText: { color: theme.colors.mutedInk, fontSize: 13 },
  input: { minHeight: 44, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.colors.line, backgroundColor: '#fff', paddingHorizontal: 12, paddingVertical: 10, color: theme.colors.ink, fontSize: 14 },
  textarea: { minHeight: 96, textAlignVertical: 'top' },
  switchRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  switchLabel: { color: theme.colors.ink, fontSize: 14, fontWeight: '600' },
  suggestionList: { gap: 6 },
  suggestionItem: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  suggestionTitle: { color: theme.colors.ink, fontSize: 14, fontWeight: '600' },
  suggestionMeta: { color: theme.colors.mutedInk, fontSize: 12, marginTop: 2 },
});
