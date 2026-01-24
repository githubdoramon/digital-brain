import React, { useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { theme } from '@/theme';

type ContactRelationship = {
  relationship_id: string;
  contact_id: string;
  type: string;
  other_type: string | null;
};

type Relationship = {
  relationship_id: string;
  from_contact_id: string;
  to_contact_id: string;
  relationship_type: string;
  reciprocal_type: string | null;
};

type Contact = {
  contact_id: string;
  display_name: string;
  relationships: ContactRelationship[];
  aliases: string[];
  emails: string[];
  phones: string[];
  links: string[];
  tags: string[];
  comments: string;
  birthday: string | null;
  external_id: string | null;
};

const buildRelationshipId = (fromId: string, toId: string) => `rel_${fromId}_${toId}`;

export default function RelationshipManagementScreen() {
  const { contactId } = useLocalSearchParams<{ contactId: string }>();
  const authFetch = apiFetch;
  const insets = useSafeAreaInsets();
  const [contact, setContact] = useState<Contact | null>(null);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [allContacts, setAllContacts] = useState<Contact[]>([]);
  const [search, setSearch] = useState('');
  const [selectedContactId, setSelectedContactId] = useState<string | null>(null);
  const [newType, setNewType] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const result = (await authFetch(`/mobile/contacts/${contactId}`)) as Contact;
        if (mounted) {
          setContact(result);
          setRelationships(
            (result.relationships || []).map((rel) => ({
              relationship_id: rel.relationship_id,
              from_contact_id: contactId,
              to_contact_id: rel.contact_id,
              relationship_type: rel.type,
              reciprocal_type: rel.other_type ?? null,
            })),
          );
        }
      } catch (error) {
        console.warn('[relationships] load failed', error);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [contactId]);

  useEffect(() => {
    (async () => {
      try {
        const result = (await authFetch('/mobile/contacts')) as { contacts: Contact[] };
        setAllContacts(result.contacts ?? []);
      } catch (error) {
        console.warn('[relationships] contacts load failed', error);
      }
    })();
  }, []);

  const relationshipSnapshot = useMemo(
    () => JSON.stringify(relationships.map((rel) => [rel.to_contact_id, rel.relationship_type])),
    [relationships],
  );

  const originalSnapshot = useMemo(() => {
    if (!contact) return '';
    const base = (contact.relationships || []).map((rel) => [rel.contact_id, rel.type]);
    return JSON.stringify(base);
  }, [contact]);

  const isDirty = relationshipSnapshot !== originalSnapshot;

  const availableContacts = useMemo(() => {
    const lower = search.trim().toLowerCase();
    return allContacts
      .filter((item) => item.contact_id !== contactId)
      .filter((item) => item.display_name.toLowerCase().includes(lower));
  }, [allContacts, contactId, search]);

  const handleAddRelationship = () => {
    if (!selectedContactId || !newType || !contact) return;
    const relationshipId = buildRelationshipId(contact.contact_id, selectedContactId);
    const updated = [
      ...relationships,
      {
        relationship_id: relationshipId,
        from_contact_id: contact.contact_id,
        to_contact_id: selectedContactId,
        relationship_type: newType,
        reciprocal_type: null,
      },
    ];
    setRelationships(updated);
    setSelectedContactId(null);
    setNewType('');
    setSearch('');
  };

  const handleSave = async () => {
    if (!contact) return;
    setIsSaving(true);
    try {
      await authFetch('/mobile/ingest/contact', {
        method: 'POST',
        body: JSON.stringify({
          contact_id: contact.contact_id,
          display_name: contact.display_name,
          aliases: contact.aliases ?? [],
          birthday: contact.birthday,
          emails: contact.emails,
          phones: contact.phones,
          links: contact.links,
          tags: contact.tags,
          comments: contact.comments,
          external_id: contact.external_id,
          relationships,
        }),
      });
      const refreshed = (await authFetch(`/mobile/contacts/${contact.contact_id}`)) as Contact;
      setContact(refreshed);
      setRelationships(
        (refreshed.relationships || []).map((rel) => ({
          relationship_id: rel.relationship_id,
          from_contact_id: contactId,
          to_contact_id: rel.contact_id,
          relationship_type: rel.type,
          reciprocal_type: rel.other_type ?? null,
        })),
      );
    } catch (error) {
      console.warn('[relationships] save failed', error);
    } finally {
      setIsSaving(false);
    }
  };

  if (!contact) {
    return <View style={styles.container} />;
  }

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={[styles.content, { paddingTop: insets.top + 16 }]}>
        <Text style={styles.title}>Relationships</Text>
        <Text style={styles.subtitle}>Keep the relationships for {contact.display_name} up to date.</Text>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Existing</Text>
          {relationships.length === 0 ? (
            <Text style={styles.muted}>No relationships yet.</Text>
          ) : (
            relationships.map((rel) => {
              const name = allContacts.find((item) => item.contact_id === rel.to_contact_id)?.display_name;
              return (
                <View key={rel.relationship_id} style={styles.row}>
                  <Text style={styles.rowTitle}>{name ?? 'Unknown contact'}</Text>
                  <TextInput
                    style={styles.input}
                    value={rel.relationship_type}
                    onChangeText={(value) =>
                      setRelationships((prev) =>
                        prev.map((item) =>
                          item.relationship_id === rel.relationship_id
                            ? { ...item, relationship_type: value }
                            : item,
                        ),
                      )
                    }
                    placeholder="relationship type"
                    placeholderTextColor={theme.colors.mutedInk}
                  />
                </View>
              );
            })
          )}
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Add new</Text>
          <TextInput
            style={styles.input}
            value={search}
            onChangeText={(value) => {
              setSearch(value);
              setSelectedContactId(null);
            }}
            placeholder="Search contacts"
            placeholderTextColor={theme.colors.mutedInk}
          />
          {search.length > 0 && (
            <View style={styles.suggestions}>
              {availableContacts.slice(0, 6).map((item) => (
                <Pressable
                  key={item.contact_id}
                  style={[
                    styles.suggestion,
                    selectedContactId === item.contact_id && styles.suggestionActive,
                  ]}
                  onPress={() => setSelectedContactId(item.contact_id)}
                >
                  <Text style={styles.suggestionText}>{item.display_name}</Text>
                </Pressable>
              ))}
            </View>
          )}
          <TextInput
            style={styles.input}
            value={newType}
            onChangeText={setNewType}
            placeholder="Relationship type (friend, colleague, etc)"
            placeholderTextColor={theme.colors.mutedInk}
          />
          <Pressable style={styles.addButton} onPress={handleAddRelationship}>
            <Text style={styles.addButtonText}>Add relationship</Text>
          </Pressable>
        </View>
      </ScrollView>

      <FloatingSaveButton
        visible={isDirty}
        label={isSaving ? 'Saving...' : 'Save relationships'}
        onPress={handleSave}
        disabled={isSaving}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  content: {
    paddingHorizontal: 20,
    paddingBottom: 120,
    gap: 16,
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  section: {
    padding: 16,
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: theme.colors.card,
    gap: 12,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  row: {
    gap: 8,
  },
  rowTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: theme.colors.ink,
  },
  muted: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  suggestions: {
    gap: 8,
  },
  suggestion: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: theme.colors.background,
  },
  suggestionActive: {
    borderColor: theme.colors.accent,
    backgroundColor: theme.colors.paleTeal,
  },
  suggestionText: {
    fontSize: 13,
    color: theme.colors.ink,
  },
  addButton: {
    backgroundColor: theme.colors.ink,
    paddingVertical: 12,
    borderRadius: theme.radius.md,
    alignItems: 'center',
  },
  addButtonText: {
    color: '#fff',
    fontWeight: '600',
  },
});
