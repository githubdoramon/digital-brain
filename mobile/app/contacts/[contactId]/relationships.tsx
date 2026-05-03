import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import Ionicons from '@expo/vector-icons/Ionicons';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';
import { matchesContactSearch } from '@/utils/contactSearch';

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

function normalizeRouteParam(value: string | undefined): string {
  if (!value) return '';
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export default function RelationshipManagementScreen() {
  const { contactId } = useLocalSearchParams<{ contactId: string }>();
  const contactParamRaw = Array.isArray(contactId) ? contactId[0] : contactId;
  const contactParam = normalizeRouteParam(contactParamRaw);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showSuccess, showError } = useAppNotice();
  const [contact, setContact] = useState<Contact | null>(null);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [allContacts, setAllContacts] = useState<Contact[]>([]);
  const [search, setSearch] = useState('');
  const [selectedContactId, setSelectedContactId] = useState<string | null>(null);
  const [newType, setNewType] = useState('');
  const [newReciprocal, setNewReciprocal] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [deletedRelationshipIds, setDeletedRelationshipIds] = useState<string[]>([]);
  const [existingRelationshipIds, setExistingRelationshipIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    let mounted = true;
    if (!contactParam) {
      return () => {
        mounted = false;
      };
    }
    (async () => {
      setIsLoading(true);
      setLoadError(null);
      try {
        const result = (await apiFetch(`/mobile/contacts/${encodeURIComponent(contactParam)}`)) as Contact;
        if (mounted) {
          setContact(result);
          setRelationships(
            (result.relationships || []).map((rel) => ({
              relationship_id: rel.relationship_id,
              from_contact_id: contactParam,
              to_contact_id: rel.contact_id,
              relationship_type: rel.type,
              reciprocal_type: rel.other_type ?? rel.type,
            })),
          );
          setExistingRelationshipIds(
            new Set((result.relationships || []).map((rel) => rel.relationship_id)),
          );
        }
      } catch (error) {
        console.warn('[relationships] load failed', error);
        if (mounted) {
          setLoadError('Unable to load contact relationships.');
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [contactParam]);

  useEffect(() => {
    (async () => {
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        setAllContacts(result.contacts ?? []);
      } catch (error) {
        console.warn('[relationships] contacts load failed', error);
      }
    })();
  }, []);

  useEffect(() => {
    const showSubscription = Keyboard.addListener('keyboardDidShow', () => {
      setKeyboardVisible(true);
    });
    const hideSubscription = Keyboard.addListener('keyboardDidHide', () => {
      setKeyboardVisible(false);
    });
    return () => {
      showSubscription.remove();
      hideSubscription.remove();
    };
  }, []);

  const relationshipSnapshot = useMemo(
    () =>
      JSON.stringify(
        relationships.map((rel) => [rel.to_contact_id, rel.relationship_type, rel.reciprocal_type ?? '']),
      ),
    [relationships],
  );

  const originalSnapshot = useMemo(() => {
    if (!contact) return '';
    const base = (contact.relationships || []).map((rel) => [rel.contact_id, rel.type, rel.other_type ?? '']);
    return JSON.stringify(base);
  }, [contact]);

  const isDirty = relationshipSnapshot !== originalSnapshot;

  const availableContacts = useMemo(() => {
    const query = search.trim();
    return allContacts
      .filter((item) => item.contact_id !== contactParam)
      .filter((item) => matchesContactSearch(item, query));
  }, [allContacts, contactParam, search]);

  const hasInvalidSelection = search.trim().length > 0 && !selectedContactId;

  const handleAddRelationship = () => {
    if (!selectedContactId || !newType || !contact) {
      if (!selectedContactId && search.trim().length > 0) {
        Alert.alert('Select a contact', 'Choose an existing contact from the list.');
      }
      return;
    }
    const relationshipId = buildRelationshipId(contact.contact_id, selectedContactId);
    const updated = [
      ...relationships,
      {
        relationship_id: relationshipId,
        from_contact_id: contact.contact_id,
        to_contact_id: selectedContactId,
        relationship_type: newType,
        reciprocal_type: newReciprocal || newType,
      },
    ];
    setRelationships(updated);
    setSelectedContactId(null);
    setNewType('');
    setNewReciprocal('');
    setSearch('');
  };

  const handleSave = async () => {
    if (!contact) return;
    if (hasInvalidSelection) {
      Alert.alert('Select a contact', 'Choose an existing contact before saving.');
      return;
    }
    setIsSaving(true);
    try {
      const encodedContact = encodeURIComponent(contact.contact_id);
      await Promise.all([
        ...deletedRelationshipIds.map((relationshipId) =>
          apiFetch(`/mobile/contacts/${encodedContact}/relationships/${encodeURIComponent(relationshipId)}`,
            {
              method: 'DELETE',
            },
          ),
        ),
        ...relationships.map((rel) =>
          apiFetch(`/mobile/contacts/${encodedContact}/relationships`, {
            method: 'POST',
            body: JSON.stringify({
              relationship_id: rel.relationship_id,
              from_contact_id: contact.contact_id,
              to_contact_id: rel.to_contact_id,
              relationship_type: rel.relationship_type,
              reciprocal_type: rel.reciprocal_type ?? rel.relationship_type,
            }),
          }),
        ),
      ]);

      const refreshed = (await apiFetch(`/mobile/contacts/${encodedContact}`)) as Contact;
      setContact(refreshed);
      setRelationships(
        (refreshed.relationships || []).map((rel) => ({
          relationship_id: rel.relationship_id,
          from_contact_id: contactParam ?? '',
          to_contact_id: rel.contact_id,
          relationship_type: rel.type,
          reciprocal_type: rel.other_type ?? null,
        })),
      );
      setExistingRelationshipIds(
        new Set((refreshed.relationships || []).map((rel) => rel.relationship_id)),
      );
      setDeletedRelationshipIds([]);
      showSuccess('Relationships updated.');
      router.back();
    } catch (error) {
      console.warn('[relationships] save failed', error);
      showError('Unable to save relationships. Please try again.');
    } finally {
      setIsSaving(false);
    }
  };

  if (!contact) {
    return (
      <View style={styles.container}>
        <View style={styles.emptyState}>
          <Text style={styles.emptyTitle}>
            {isLoading ? 'Loading relationships...' : 'Relationships unavailable'}
          </Text>
          {loadError ? <Text style={styles.emptySubtitle}>{loadError}</Text> : null}
        </View>
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
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: insets.top + 56,
            paddingBottom: insets.bottom + (keyboardVisible ? 16 : 32),
          },
        ]}
        keyboardShouldPersistTaps="always"
        keyboardDismissMode="none"
      >
        <Text style={styles.title}>Relationships</Text>
        <Text style={styles.subtitle}>Keep the relationships for {contact.display_name} up to date.</Text>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Existing</Text>
          {relationships.length === 0 ? (
            <Text style={styles.muted}>No relationships yet.</Text>
          ) : (
            relationships.map((rel) => {
              const name = allContacts.find((item) => item.contact_id === rel.to_contact_id)?.display_name;
              return (
                <View key={rel.relationship_id} style={styles.row}>
                  <View style={styles.rowHeader}>
                    <Text style={styles.rowTitle}>{name ?? 'Unknown contact'}</Text>
                    <Pressable
                      onPress={() => {
                        setRelationships((prev) =>
                          prev.filter((item) => item.relationship_id !== rel.relationship_id),
                        );
                        if (existingRelationshipIds.has(rel.relationship_id)) {
                          setDeletedRelationshipIds((prev) =>
                            prev.includes(rel.relationship_id)
                              ? prev
                              : [...prev, rel.relationship_id],
                          );
                        }
                      }}
                      style={styles.deleteButton}
                    >
                      <Ionicons name="trash-outline" size={18} color={theme.colors.mutedInk} />
                    </Pressable>
                  </View>
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
                  <TextInput
                    style={styles.input}
                    value={rel.reciprocal_type ?? ''}
                    onChangeText={(value) =>
                      setRelationships((prev) =>
                        prev.map((item) =>
                          item.relationship_id === rel.relationship_id
                            ? { ...item, reciprocal_type: value }
                            : item,
                        ),
                      )
                    }
                    placeholder="reciprocal type"
                    placeholderTextColor={theme.colors.mutedInk}
                  />
                </View>
              );
            })
          )}
        </Card>

        <Card style={styles.section}>
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
          <TextInput
            style={styles.input}
            value={newReciprocal}
            onChangeText={setNewReciprocal}
            placeholder="Reciprocal type (e.g., manager)"
            placeholderTextColor={theme.colors.mutedInk}
          />
          <Pressable style={styles.addButton} onPress={handleAddRelationship}>
            <Text style={styles.addButtonText}>Add relationship</Text>
          </Pressable>
        </Card>
      </ScrollView>

      <FloatingSaveButton
        visible={isDirty}
        label={isSaving ? 'Saving...' : 'Save relationships'}
        onPress={handleSave}
        disabled={isSaving || hasInvalidSelection}
        loading={isSaving}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
    gap: 8,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  emptySubtitle: {
    fontSize: 14,
    color: theme.colors.mutedInk,
    textAlign: 'center',
  },
  content: {
    paddingHorizontal: 20,
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
  rowHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  rowTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  deleteButton: {
    padding: 4,
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
