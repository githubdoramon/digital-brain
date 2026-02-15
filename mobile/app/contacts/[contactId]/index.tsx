import Ionicons from '@expo/vector-icons/Ionicons';
import DateTimePicker, { DateType, useDefaultStyles } from 'react-native-ui-datepicker';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Animated,
  Keyboard,
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Avatar } from '@/components/Avatar';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
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

function formatIsoDate(value: Date) {
  return value.toISOString().slice(0, 10);
}

function resolvePickerDate(value: DateType): Date | null {
  if (!value) return null;
  if (value instanceof Date) return value;
  if (typeof value === 'string' || typeof value === 'number') {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  const maybeDayjs = value as { toDate?: () => Date };
  if (typeof maybeDayjs.toDate === 'function') {
    const parsed = maybeDayjs.toDate();
    return parsed instanceof Date && !Number.isNaN(parsed.getTime()) ? parsed : null;
  }
  return null;
}

function createEmptyContact(contactId: string): Contact {
  return {
    contact_id: contactId,
    display_name: '',
    aliases: [],
    emails: [],
    phones: [],
    links: [],
    tags: [],
    comments: '',
    birthday: null,
    external_id: null,
    avatar_url: null,
    relationships: [],
  };
}

function buildContactId(draft: Contact): string {
  const source = draft.display_name.trim() || draft.emails[0] || draft.phones[0] || 'contact';
  const slug = source
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32);
  const suffix = Date.now().toString(36);
  return `${slug || 'contact'}-${suffix}`;
}

function normalizeRouteParam(value: string | undefined): string {
  if (!value) return '';
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export default function ContactDetailScreen() {
  const { contactId } = useLocalSearchParams<{ contactId: string }>();
  const contactParamRaw = Array.isArray(contactId) ? contactId[0] : contactId;
  const contactParam = normalizeRouteParam(contactParamRaw);
  const isCreating = !contactParam || contactParam === 'new';
  const { token } = useAuth();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [contact, setContact] = useState<Contact | null>(null);
  const [draft, setDraft] = useState<Contact | null>(null);
  const [aliasesText, setAliasesText] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [contactsIndex, setContactsIndex] = useState<Map<string, string>>(new Map());
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [draftDate, setDraftDate] = useState<Date | null>(null);
  const defaultPickerStyles = useDefaultStyles('light');

  const heroOpacity = useRef(new Animated.Value(0)).current;
  const heroTranslate = useRef(new Animated.Value(12)).current;

  useEffect(() => {
    if (!isCreating) {
      return;
    }
    const emptyContact = createEmptyContact(contactParam ?? 'new');
    setContact(emptyContact);
    setDraft(emptyContact);
    setAliasesText(listToText(emptyContact.aliases));
  }, [contactParam, isCreating]);

  useEffect(() => {
    let mounted = true;
    if (!contactParam || isCreating) {
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
          setDraft(result);
          setAliasesText(listToText(result.aliases ?? []));
        }
      } catch (error) {
        console.warn('[contacts] detail load failed', error);
        if (mounted) {
          setLoadError('Unable to load this contact.');
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
  }, [contactParam, isCreating]);

  useEffect(() => {
    (async () => {
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        const map = new Map<string, string>();
        result.contacts.forEach((item) => map.set(item.contact_id, item.display_name));
        setContactsIndex(map);
      } catch (error) {
        console.warn('[contacts] map load failed', error);
      }
    })();
  }, []);

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

  const relationshipChips = useMemo(() => {
    if (!contact) return [];
    return (contact.relationships || []).slice(0, 6).map((rel) => {
      const name = contactsIndex.get(rel.contact_id) ?? 'Unknown';
      return { label: `${rel.type} · ${name}` };
    });
  }, [contact, contactsIndex]);

  const birthdayValue = draft?.birthday ?? '';
  const pickerDate = useMemo(() => {
    if (!birthdayValue) return new Date();
    const parsed = new Date(`${birthdayValue}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
  }, [birthdayValue]);
  const activePickerDate = draftDate ?? pickerDate;

  const isDirty = useMemo(() => {
    if (!draft) return false;
    const parsedAliases = textToList(aliasesText);
    if (isCreating) {
      return Boolean(
        draft.display_name.trim() ||
          parsedAliases.length ||
          draft.emails.length ||
          draft.phones.length ||
          draft.links.length ||
          draft.tags.length ||
          draft.comments.trim() ||
          draft.birthday,
      );
    }
    if (!contact) return false;
    const base = {
      display_name: contact.display_name,
      aliases: contact.aliases,
      emails: contact.emails,
      phones: contact.phones,
      links: contact.links,
      tags: contact.tags,
      comments: contact.comments,
      birthday: contact.birthday,
    };
    const current = {
      display_name: draft.display_name,
      aliases: parsedAliases,
      emails: draft.emails,
      phones: draft.phones,
      links: draft.links,
      tags: draft.tags,
      comments: draft.comments,
      birthday: draft.birthday,
    };
    return JSON.stringify(base) !== JSON.stringify(current);
  }, [contact, draft, isCreating, aliasesText]);

  const handleSave = async () => {
    if (!draft) return;
    setIsSaving(true);
    try {
      const targetContactId = isCreating ? buildContactId(draft) : contact?.contact_id;
      if (!targetContactId) {
        return;
      }
      const normalizedName = draft.display_name.trim();
      await apiFetch('/mobile/ingest/contact', {
        method: 'POST',
        body: JSON.stringify({
          contact_id: targetContactId,
          display_name: normalizedName || 'New contact',
          aliases: textToList(aliasesText),
          birthday: draft.birthday ? draft.birthday : null,
          emails: draft.emails,
          phones: draft.phones,
          links: draft.links,
          tags: draft.tags,
          comments: draft.comments,
          external_id: contact?.external_id ?? null,
        }),
      });
      const refreshed = (await apiFetch(
        `/mobile/contacts/${encodeURIComponent(targetContactId)}`,
      )) as Contact;
      setContact(refreshed);
      setDraft(refreshed);
      setAliasesText(listToText(refreshed.aliases ?? []));
      if (isCreating) {
        router.replace(`/contacts/${encodeURIComponent(targetContactId)}`);
      }
    } catch (error) {
      console.warn('[contacts] save failed', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleOpenDatePicker = () => {
    setDraftDate(pickerDate);
    setShowDatePicker(true);
  };

  const handleCloseDatePicker = () => {
    setShowDatePicker(false);
    setDraftDate(null);
  };

  const handleConfirmDate = () => {
    if (draftDate && draft) {
      setDraft({ ...draft, birthday: formatIsoDate(draftDate) });
    }
    handleCloseDatePicker();
  };

  const handleDelete = () => {
    if (!contact || isCreating) return;
    Alert.alert(
      'Delete contact',
      `Are you sure you want to delete ${contact.display_name || 'this contact'}? This action cannot be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            setIsDeleting(true);
            try {
              await apiFetch(`/mobile/contacts/${encodeURIComponent(contact.contact_id)}`, {
                method: 'DELETE',
              });
              router.back();
            } catch (error) {
              console.warn('[contacts] delete failed', error);
              Alert.alert('Error', 'Unable to delete this contact. Please try again.');
            } finally {
              setIsDeleting(false);
            }
          },
        },
      ],
    );
  };

  if (!draft) {
    return (
      <View style={styles.container}>
        <View style={styles.emptyState}>
          <Text style={styles.emptyTitle}>{isLoading ? 'Loading contact...' : 'Contact unavailable'}</Text>
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
        <Animated.View style={[styles.hero, { opacity: heroOpacity, transform: [{ translateY: heroTranslate }] }]}>
          <Avatar name={draft.display_name} uri={draft.avatar_url ?? undefined} token={token} size={88} />
          <View style={styles.heroText}>
            <TextInput
              style={styles.nameInput}
              value={draft.display_name}
              onChangeText={(value) => setDraft({ ...draft, display_name: value })}
              placeholder="Full name"
              placeholderTextColor={theme.colors.mutedInk}
            />
            <Text style={styles.heroSubtitle}>
              {isCreating ? 'Add details for this new contact.' : 'Tap any field to edit.'}
            </Text>
          </View>
        </Animated.View>

        {!isCreating ? (
          <Card style={styles.section}>
            <Text style={styles.sectionTitle}>Relationship overview</Text>
            <RelationshipChips chips={relationshipChips} />
            <Pressable
              style={styles.linkButton}
              onPress={() =>
                router.push({
                  pathname: '/contacts/[contactId]/relationships',
                  params: { contactId: contactParam ?? '' },
                })
              }
            >
              <Text style={styles.linkText}>Manage relationships</Text>
            </Pressable>
          </Card>
        ) : null}

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Contact</Text>
          <ContactActionMenu emails={draft.emails} phones={draft.phones} />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Aliases</Text>
          <TextInput
            style={styles.input}
            value={aliasesText}
            onChangeText={setAliasesText}
            placeholder="nicknames, alternate names"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Emails</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.emails)}
            onChangeText={(value) => setDraft({ ...draft, emails: textToList(value) })}
            placeholder="add emails, comma separated"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Phones</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.phones)}
            onChangeText={(value) => setDraft({ ...draft, phones: textToList(value) })}
            placeholder="add phone numbers"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Birthday</Text>
          <Pressable
            onPress={handleOpenDatePicker}
            style={({ pressed }) => [styles.dateField, pressed && styles.dateFieldPressed]}
          >
            <Text style={birthdayValue ? styles.dateFieldText : styles.dateFieldPlaceholder}>
              {birthdayValue || 'YYYY-MM-DD'}
            </Text>
            <Ionicons name="calendar" size={18} color={theme.colors.accentDeep} />
          </Pressable>
          {birthdayValue ? (
            <Pressable onPress={() => setDraft({ ...draft, birthday: null })}>
              <Text style={styles.clearText}>Clear birthday</Text>
            </Pressable>
          ) : null}
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Links</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.links)}
            onChangeText={(value) => setDraft({ ...draft, links: textToList(value) })}
            placeholder="websites, social, etc"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Tags</Text>
          <TextInput
            style={styles.input}
            value={listToText(draft.tags)}
            onChangeText={(value) => setDraft({ ...draft, tags: textToList(value) })}
            placeholder="tags"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Notes</Text>
          <TextInput
            style={[styles.input, styles.textArea]}
            value={draft.comments}
            onChangeText={(value) => setDraft({ ...draft, comments: value })}
            placeholder="Add a few notes"
            placeholderTextColor={theme.colors.mutedInk}
            multiline
          />
        </Card>

        {!isCreating ? (
          <Button
            label={isDeleting ? 'Deleting...' : 'Delete contact'}
            variant="danger"
            onPress={handleDelete}
            disabled={isDeleting}
          />
        ) : null}
      </ScrollView>

      <FloatingSaveButton
        visible={isDirty}
        label={isSaving ? (isCreating ? 'Creating...' : 'Saving...') : isCreating ? 'Create contact' : 'Save changes'}
        onPress={handleSave}
        disabled={isSaving}
        loading={isSaving}
      />
      <Modal
        visible={showDatePicker}
        transparent
        animationType="slide"
        onRequestClose={handleCloseDatePicker}
      >
        <View style={styles.modalContainer} pointerEvents="box-none">
          <Pressable style={styles.modalBackdrop} onPress={handleCloseDatePicker} />
          <View style={styles.modalSheet} pointerEvents="auto">
            <View style={styles.datePickerHeader}>
              <Pressable
                onPress={handleCloseDatePicker}
                style={({ pressed }) => [
                  styles.datePickerAction,
                  pressed && styles.datePickerDonePressed,
                ]}
              >
                <Text style={styles.datePickerCancelText}>Cancel</Text>
              </Pressable>
              <Text style={styles.datePickerTitle}>Pick a birthday</Text>
              <Pressable
                onPress={handleConfirmDate}
                style={({ pressed }) => [
                  styles.datePickerAction,
                  pressed && styles.datePickerDonePressed,
                ]}
              >
                <Text style={styles.datePickerDoneText}>Done</Text>
              </Pressable>
            </View>
            <DateTimePicker
              mode="single"
              date={activePickerDate}
              onChange={({ date }) => {
                const resolved = resolvePickerDate(date);
                if (resolved) {
                  setDraftDate(resolved);
                }
              }}
              styles={{
                ...defaultPickerStyles,
                today: {
                  ...defaultPickerStyles.today,
                  borderColor: theme.colors.accent,
                },
                selected: {
                  ...defaultPickerStyles.selected,
                  backgroundColor: theme.colors.accent,
                },
                selected_label: {
                  ...defaultPickerStyles.selected_label,
                  color: '#fff',
                },
                day: {
                  ...defaultPickerStyles.day,
                  borderRadius: 10,
                },
              }}
              style={styles.datePicker}
            />
          </View>
        </View>
      </Modal>
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
  dateField: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.md,
    paddingHorizontal: 14,
    paddingVertical: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  dateFieldPressed: {
    borderColor: theme.colors.accent,
  },
  dateFieldText: {
    fontSize: 14,
    color: theme.colors.ink,
  },
  dateFieldPlaceholder: {
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  clearText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.accentDeep,
  },
  modalBackdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(15, 18, 20, 0.3)',
    zIndex: 1,
  },
  modalContainer: {
    flex: 1,
    justifyContent: 'flex-end',
  },
  modalSheet: {
    backgroundColor: '#fff',
    paddingTop: 12,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    paddingBottom: 12,
    zIndex: 2,
  },
  datePickerHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  datePickerTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  datePickerAction: {
    paddingVertical: 6,
    paddingHorizontal: 4,
  },
  datePickerDonePressed: {
    opacity: 0.7,
  },
  datePickerDoneText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.accentDeep,
  },
  datePickerCancelText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.mutedInk,
  },
  datePicker: {
    height: 360,
  },
});
