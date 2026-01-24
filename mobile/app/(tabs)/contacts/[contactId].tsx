import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useAuth } from '@/auth/AuthContext';
import { Avatar } from '@/components/Avatar';
import { ContactActionMenu } from '@/components/ContactActionMenu';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { RelationshipChips } from '@/components/RelationshipChips';
import { theme } from '@/theme';

type Relationship = {
  relationship_id: string;
  contact_id: string;
  type: string;
  other_type: string | null;
  direction: 'incoming' | 'outgoing';
};

type Contact = {
  contact_id: string;
  display_name: string;
  aliases: string[];
  emails: string[];
  phones: string[];
  links: string[];
  tags: string[];
  comments: string;
  birthday: string | null;
  external_id: string | null;
  avatar_url?: string | null;
  relationships: Relationship[];
};

const listToText = (items: string[]) => items.join(', ');
const textToList = (value: string) =>
  value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);

export default function ContactDetailScreen() {
  const { contactId } = useLocalSearchParams<{ contactId: string }>();
  const { authFetch, token } = useAuth();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [contact, setContact] = useState<Contact | null>(null);
  const [draft, setDraft] = useState<Contact | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [contactsIndex, setContactsIndex] = useState<Map<string, string>>(new Map());

  const heroOpacity = useRef(new Animated.Value(0)).current;
  const heroTranslate = useRef(new Animated.Value(12)).current;

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const result = (await authFetch(`/contacts/${contactId}`)) as Contact;
        if (mounted) {
          setContact(result);
          setDraft(result);
        }
      } catch (error) {
        console.warn('[contacts] detail load failed', error);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [authFetch, contactId]);

  useEffect(() => {
    (async () => {
      try {
        const result = (await authFetch('/contacts')) as { contacts: Contact[] };
        const map = new Map<string, string>();
        result.contacts.forEach((item) => map.set(item.contact_id, item.display_name));
        setContactsIndex(map);
      } catch (error) {
        console.warn('[contacts] map load failed', error);
      }
    })();
  }, [authFetch]);

  useEffect(() => {
    Animated.parallel([
      Animated.timing(heroOpacity, {
        toValue: 1,
        duration: 300,
        useNativeDriver: true,
      }),
      Animated.timing(heroTranslate, {
        toValue: 0,
        duration: 300,
        useNativeDriver: true,
      }),
    ]).start();
  }, [heroOpacity, heroTranslate]);

  const relationshipChips = useMemo(() => {
    if (!contact) return [];
    return (contact.relationships || []).slice(0, 6).map((rel) => {
      const name = contactsIndex.get(rel.contact_id) ?? 'Unknown';
      return { label: `${rel.type} · ${name}` };
    });
  }, [contact, contactsIndex]);

  const isDirty = useMemo(() => {
    if (!contact || !draft) return false;
    const base = {
      display_name: contact.display_name,
      emails: contact.emails,
      phones: contact.phones,
      links: contact.links,
      tags: contact.tags,
      comments: contact.comments,
      birthday: contact.birthday,
    };
    const current = {
      display_name: draft.display_name,
      emails: draft.emails,
      phones: draft.phones,
      links: draft.links,
      tags: draft.tags,
      comments: draft.comments,
      birthday: draft.birthday,
    };
    return JSON.stringify(base) !== JSON.stringify(current);
  }, [contact, draft]);

  const handleSave = async () => {
    if (!draft || !contact) return;
    setIsSaving(true);
    try {
      await authFetch('/ingest/contact', {
        method: 'POST',
        body: JSON.stringify({
          contact_id: contact.contact_id,
          display_name: draft.display_name,
          aliases: contact.aliases ?? [],
          birthday: draft.birthday ? draft.birthday : null,
          emails: draft.emails,
          phones: draft.phones,
          links: draft.links,
          tags: draft.tags,
          comments: draft.comments,
          external_id: contact.external_id,
          relationships: contact.relationships,
        }),
      });
      const refreshed = (await authFetch(`/contacts/${contact.contact_id}`)) as Contact;
      setContact(refreshed);
      setDraft(refreshed);
    } catch (error) {
      console.warn('[contacts] save failed', error);
    } finally {
      setIsSaving(false);
    }
  };

  if (!draft) {
    return <View style={styles.container} />;
  }

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={[styles.content, { paddingTop: insets.top + 16 }]}>
        <Animated.View style={[styles.hero, { opacity: heroOpacity, transform: [{ translateY: heroTranslate }] }]}>
          <Avatar name={draft.display_name} uri={draft.avatar_url ?? undefined} token={token} size={88} />
          <View style={styles.heroText}>
            <TextInput
              style={styles.nameInput}
              value={draft.display_name}
              onChangeText={(value) => setDraft({ ...draft, display_name: value })}
              placeholder="Name"
              placeholderTextColor={theme.colors.mutedInk}
            />
            <Text style={styles.heroSubtitle}>Tap any field to edit.</Text>
          </View>
        </Animated.View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Relationship overview</Text>
          <RelationshipChips chips={relationshipChips} />
          <Pressable
            style={styles.linkButton}
            onPress={() => router.push(`/(tabs)/contacts/${contactId}/relationships`)}
          >
            <Text style={styles.linkText}>Manage relationships</Text>
          </Pressable>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Contact</Text>
          <ContactActionMenu emails={draft.emails} phones={draft.phones} />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Emails</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.emails)}
            onChangeText={(value) => setDraft({ ...draft, emails: textToList(value) })}
            placeholder="add emails, comma separated"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Phones</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.phones)}
            onChangeText={(value) => setDraft({ ...draft, phones: textToList(value) })}
            placeholder="add phone numbers"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Links</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.links)}
            onChangeText={(value) => setDraft({ ...draft, links: textToList(value) })}
            placeholder="websites, social, etc"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Tags</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.tags)}
            onChangeText={(value) => setDraft({ ...draft, tags: textToList(value) })}
            placeholder="tags"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Notes</Text>
          <TextInput
            style={[styles.input, styles.textArea]}
            value={draft.comments}
            onChangeText={(value) => setDraft({ ...draft, comments: value })}
            placeholder="Add a few notes"
            placeholderTextColor={theme.colors.mutedInk}
            multiline
          />
        </View>
      </ScrollView>

      <FloatingSaveButton
        visible={isDirty}
        label={isSaving ? 'Saving...' : 'Save changes'}
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
    gap: 20,
  },
  hero: {
    flexDirection: 'row',
    gap: 16,
    alignItems: 'center',
  },
  heroText: {
    flex: 1,
    gap: 6,
  },
  nameInput: {
    fontSize: 24,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  heroSubtitle: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  section: {
    gap: 10,
    padding: 16,
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  linkButton: {
    alignSelf: 'flex-start',
  },
  linkText: {
    fontSize: 13,
    color: theme.colors.accentDeep,
    fontWeight: '600',
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: theme.colors.ink,
  },
  textArea: {
    minHeight: 110,
    textAlignVertical: 'top',
  },
});
