import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { UiDirectiveDateTimePickerSheet } from '@/components/ui-directive-card/UiDirectiveDateTimePickerSheet';
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

function floatingOffset(insetBottom: number, keyboardHeight: number) {
  const keyboardInset =
    Platform.OS === 'ios' ? Math.max(0, keyboardHeight - insetBottom) : keyboardHeight;
  return insetBottom + 20 + keyboardInset;
}

function buildPlaceSearchText(place: EventPlaceOption): string {
  return [place.name, place.address, place.city, place.country, ...(place.aliases || [])]
    .filter(Boolean)
    .join(' ');
}

function formatPlaceLabel(place: EventPlaceOption): string {
  const parts = [place.name, place.city, place.country]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  return parts.length > 0 ? parts.join(', ') : place.place_id;
}

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

function removeContact(draft: ContactProposalDraft, proposalId: string): ContactProposalDraft {
  const contact = draft.contacts.find((item) => item.proposalId === proposalId);
  if (!contact) return draft;
  return {
    ...draft,
    contacts: draft.contacts.filter((item) => item.proposalId !== proposalId),
    relationships: draft.relationships.filter(
      (relationship) =>
        relationship.fromReference !== contact.reference &&
        relationship.toReference !== contact.reference,
    ),
    placeLinks: draft.placeLinks.filter((link) => link.contactReference !== contact.reference),
  };
}

function removePlace(draft: ContactProposalDraft, proposalId: string): ContactProposalDraft {
  const place = draft.places.find((item) => item.proposalId === proposalId);
  if (!place) return draft;
  return {
    ...draft,
    places: draft.places.filter((item) => item.proposalId !== proposalId),
    placeLinks: draft.placeLinks.filter((link) => link.placeReference !== place.reference),
  };
}

function SectionHeader({
  title,
  subtitle,
  onRemove,
}: {
  title: string;
  subtitle?: string;
  onRemove?: () => void;
}) {
  return (
    <View style={styles.sectionHeader}>
      <View style={styles.sectionTitleWrap}>
        <Text style={styles.label}>{title}</Text>
        {subtitle ? <Text style={styles.helperText}>{subtitle}</Text> : null}
      </View>
      {onRemove ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Remove ${title}`}
          onPress={onRemove}
          style={({ pressed }) => [styles.iconButton, pressed && styles.iconButtonPressed]}
        >
          <Ionicons name="trash-outline" size={17} color={theme.colors.accentDeep} />
        </Pressable>
      ) : null}
    </View>
  );
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
        <Pressable
          key={contact.contact_id}
          accessibilityRole="button"
          accessibilityLabel={`Use ${contact.display_name}`}
          onPress={() => onSelect(contact)}
          style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
        >
          <View style={styles.suggestionBody}>
            <Text style={styles.suggestionText}>{contact.display_name}</Text>
            {contact.aliases?.length ? (
              <Text style={styles.suggestionMeta}>{contact.aliases.join(', ')}</Text>
            ) : null}
          </View>
          <Ionicons name="person-add-outline" size={16} color={theme.colors.accentDeep} />
        </Pressable>
      ))}
    </View>
  );
}

function ContactPickerField({
  value,
  meta,
  query,
  contacts,
  placeholder,
  onChangeText,
  onSelect,
}: {
  value: string;
  meta?: string;
  query: string;
  contacts: EventContactOption[];
  placeholder: string;
  onChangeText: (value: string) => void;
  onSelect: (contact: EventContactOption) => void;
}) {
  const [isEditing, setIsEditing] = React.useState(!value.trim());
  const trimmedValue = value.trim();

  React.useEffect(() => {
    if (!trimmedValue) {
      setIsEditing(true);
    }
  }, [trimmedValue]);

  if (!isEditing && trimmedValue) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Change ${trimmedValue}`}
        onPress={() => setIsEditing(true)}
        style={({ pressed }) => [styles.selectedEntityRow, pressed && styles.suggestionPressed]}
      >
        <View style={styles.suggestionBody}>
          <Text style={styles.selectedEntityTitle}>{trimmedValue}</Text>
          {meta ? <Text style={styles.suggestionMeta}>{meta}</Text> : null}
        </View>
        <Ionicons name="create-outline" size={17} color={theme.colors.accentDeep} />
      </Pressable>
    );
  }

  return (
    <>
      <TextInput
        value={query}
        onChangeText={onChangeText}
        onSubmitEditing={() => {
          if (query.trim()) {
            setIsEditing(false);
          }
        }}
        placeholder={placeholder}
        placeholderTextColor={theme.colors.mutedInk}
        returnKeyType="done"
        style={styles.input}
      />
      <ContactSuggestions
        query={query}
        contacts={contacts}
        onSelect={(contact) => {
          onSelect(contact);
          setIsEditing(false);
        }}
      />
    </>
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
    return places
      .filter((place) => normalizeSearch(buildPlaceSearchText(place)).includes(needle))
      .slice(0, 5);
  }, [places, query]);
  if (filtered.length === 0) return null;
  return (
    <View style={styles.suggestionList}>
      {filtered.map((place) => (
        <Pressable
          key={place.place_id}
          accessibilityRole="button"
          accessibilityLabel={`Use ${formatPlaceLabel(place)}`}
          onPress={() => onSelect(place)}
          style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
        >
          <View style={styles.suggestionBody}>
            <Text style={styles.suggestionText}>{formatPlaceLabel(place)}</Text>
            {place.address ? <Text style={styles.suggestionMeta}>{place.address}</Text> : null}
          </View>
          <Ionicons name="location-outline" size={16} color={theme.colors.accentDeep} />
        </Pressable>
      ))}
    </View>
  );
}

function PlacePickerField({
  value,
  meta,
  query,
  places,
  placeholder,
  onChangeText,
  onSelect,
}: {
  value: string;
  meta?: string;
  query: string;
  places: EventPlaceOption[];
  placeholder: string;
  onChangeText: (value: string) => void;
  onSelect: (place: EventPlaceOption) => void;
}) {
  const [isEditing, setIsEditing] = React.useState(!value.trim());
  const trimmedValue = value.trim();

  React.useEffect(() => {
    if (!trimmedValue) {
      setIsEditing(true);
    }
  }, [trimmedValue]);

  if (!isEditing && trimmedValue) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Change ${trimmedValue}`}
        onPress={() => setIsEditing(true)}
        style={({ pressed }) => [styles.selectedEntityRow, pressed && styles.suggestionPressed]}
      >
        <View style={styles.suggestionBody}>
          <Text style={styles.selectedEntityTitle}>{trimmedValue}</Text>
          {meta ? <Text style={styles.suggestionMeta}>{meta}</Text> : null}
        </View>
        <Ionicons name="create-outline" size={17} color={theme.colors.accentDeep} />
      </Pressable>
    );
  }

  return (
    <>
      <TextInput
        value={query}
        onChangeText={onChangeText}
        onSubmitEditing={() => {
          if (query.trim()) {
            setIsEditing(false);
          }
        }}
        placeholder={placeholder}
        placeholderTextColor={theme.colors.mutedInk}
        returnKeyType="done"
        style={styles.input}
      />
      <PlaceSuggestions
        query={query}
        places={places}
        onSelect={(place) => {
          onSelect(place);
          setIsEditing(false);
        }}
      />
    </>
  );
}

export function ContactDraftEditorScreen({ sessionId }: Props) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const session = React.useMemo(() => getContactDraftEditSession(sessionId), [sessionId]);
  const [draft, setDraft] = React.useState<ContactProposalDraft | null>(
    session?.initialDraft ?? null,
  );
  const [keyboardHeight, setKeyboardHeight] = React.useState(0);
  const [birthdayPickerContactId, setBirthdayPickerContactId] = React.useState<string | null>(null);

  React.useEffect(() => {
    setDraft(session?.initialDraft ?? null);
  }, [session]);

  React.useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillChangeFrame' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';
    const showListener = Keyboard.addListener(showEvent, (event) => {
      setKeyboardHeight(Math.max(0, event.endCoordinates?.height ?? 0));
    });
    const hideListener = Keyboard.addListener(hideEvent, () => {
      setKeyboardHeight(0);
    });
    return () => {
      showListener.remove();
      hideListener.remove();
    };
  }, []);

  const selectedBirthdayContact = React.useMemo(
    () => draft?.contacts.find((contact) => contact.proposalId === birthdayPickerContactId) ?? null,
    [birthdayPickerContactId, draft],
  );

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
      <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
        <View style={[styles.emptyState, { paddingTop: insets.top + 80 }]}>
          <Text style={styles.emptyTitle}>Draft editor unavailable</Text>
          <Text style={styles.emptyBody}>
            This draft has expired. Return to chat and re-open edit.
          </Text>
          <Pressable onPress={() => router.back()} style={styles.emptyAction}>
            <Text style={styles.emptyActionText}>Back to chat</Text>
          </Pressable>
        </View>
      </LinearGradient>
    );
  }

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 72 : 0}
      >
        <Animated.ScrollView
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
            useNativeDriver: false,
          })}
          scrollEventThrottle={16}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[
            styles.content,
            {
              paddingTop:
                insets.top +
                COLLAPSING_TOP_BAR_HEIGHT +
                COLLAPSING_CONTENT_TOP_PADDING +
                COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
              paddingBottom: insets.bottom + 120,
            },
          ]}
        >
          <Text style={styles.subtitle}>Review graph changes before applying them.</Text>

          {draft.contacts.map((contact) => (
            <Card key={contact.proposalId} style={styles.card}>
              <SectionHeader
                title={contact.operation === 'create' ? 'New contact' : 'Contact update'}
                subtitle={contact.source === 'derived' ? 'Inferred' : undefined}
                onRemove={() =>
                  setDraft((current) =>
                    current ? removeContact(current, contact.proposalId) : current,
                  )
                }
              />
              <ContactPickerField
                value={contact.displayName}
                query={contact.displayName}
                meta={contact.operation === 'create' ? 'New contact' : 'Existing contact'}
                contacts={session.availableContacts}
                placeholder="Search existing contact or type a new name"
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          displayName: value,
                        }))
                      : current,
                  )
                }
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

              <Text style={styles.fieldLabel}>Birth date</Text>
              <View style={styles.dateInputRow}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Select contact birth date"
                  onPress={() => setBirthdayPickerContactId(contact.proposalId)}
                  style={({ pressed }) => [
                    styles.dateField,
                    styles.dateFieldExpanded,
                    pressed && styles.dateFieldPressed,
                  ]}
                >
                  <Text style={contact.birthday ? styles.dateValue : styles.datePlaceholder}>
                    {contact.birthday || 'Add birth date'}
                  </Text>
                </Pressable>
                {contact.birthday ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Clear contact birth date"
                    onPress={() =>
                      setDraft((current) =>
                        current
                          ? updateContact(current, contact.proposalId, (item) => ({
                              ...item,
                              birthday: '',
                            }))
                          : current,
                      )
                    }
                    style={({ pressed }) => [
                      styles.clearIconButton,
                      pressed && styles.clearIconButtonPressed,
                    ]}
                  >
                    <Ionicons name="close" size={16} color={theme.colors.mutedInk} />
                  </Pressable>
                ) : null}
              </View>

              <TextInput
                value={contact.aliasesText}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          aliasesText: value,
                        }))
                      : current,
                  )
                }
                placeholder="Aliases, comma separated"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
              <TextInput
                value={contact.emailsText}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          emailsText: value,
                        }))
                      : current,
                  )
                }
                placeholder="Emails, comma separated"
                placeholderTextColor={theme.colors.mutedInk}
                keyboardType="email-address"
                style={styles.input}
              />
              <TextInput
                value={contact.phonesText}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          phonesText: value,
                        }))
                      : current,
                  )
                }
                placeholder="Phones, comma separated"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
              <TextInput
                value={contact.linksText}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          linksText: value,
                        }))
                      : current,
                  )
                }
                placeholder="Links, comma separated"
                placeholderTextColor={theme.colors.mutedInk}
                keyboardType="url"
                style={styles.input}
              />
              <TextInput
                value={contact.tagsText}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          tagsText: value,
                        }))
                      : current,
                  )
                }
                placeholder="Tags, comma separated"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
              <TextInput
                value={contact.comments}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateContact(current, contact.proposalId, (item) => ({
                          ...item,
                          comments: value,
                        }))
                      : current,
                  )
                }
                placeholder="Notes"
                placeholderTextColor={theme.colors.mutedInk}
                multiline
                style={[styles.input, styles.textarea]}
              />
            </Card>
          ))}

          {draft.relationships.map((relationship) => (
            <Card key={relationship.proposalId} style={styles.card}>
              <SectionHeader
                title={relationship.kind === 'derived' ? 'Inferred relationship' : 'Relationship'}
                onRemove={() =>
                  setDraft((current) =>
                    current
                      ? {
                          ...current,
                          relationships: current.relationships.filter(
                            (item) => item.proposalId !== relationship.proposalId,
                          ),
                        }
                      : current,
                  )
                }
              />
              <ContactPickerField
                value={relationship.fromDisplayName}
                query={relationship.fromDisplayName}
                contacts={session.availableContacts}
                placeholder="Search from contact or type a name"
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateRelationship(current, relationship.proposalId, (item) => ({
                          ...item,
                          fromDisplayName: value,
                        }))
                      : current,
                  )
                }
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
              <TextInput
                value={relationship.relationshipType}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateRelationship(current, relationship.proposalId, (item) => ({
                          ...item,
                          relationshipType: value,
                        }))
                      : current,
                  )
                }
                placeholder="Relationship type"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
              <ContactPickerField
                value={relationship.toDisplayName}
                query={relationship.toDisplayName}
                contacts={session.availableContacts}
                placeholder="Search to contact or type a name"
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updateRelationship(current, relationship.proposalId, (item) => ({
                          ...item,
                          toDisplayName: value,
                        }))
                      : current,
                  )
                }
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
                  <Text style={styles.switchLabel}>Apply inferred link</Text>
                  <Switch
                    value={relationship.enabled}
                    onValueChange={(value) =>
                      setDraft((current) =>
                        current
                          ? updateRelationship(current, relationship.proposalId, (item) => ({
                              ...item,
                              enabled: value,
                            }))
                          : current,
                      )
                    }
                  />
                </View>
              ) : null}
            </Card>
          ))}

          {draft.places.map((place) => (
            <Card key={place.proposalId} style={styles.card}>
              <SectionHeader
                title="Place"
                onRemove={() =>
                  setDraft((current) =>
                    current ? removePlace(current, place.proposalId) : current,
                  )
                }
              />
              <PlacePickerField
                value={place.name}
                query={place.name}
                meta={place.reference.startsWith('new_place:') ? 'New place' : 'Existing place'}
                places={session.availablePlaces}
                placeholder="Search existing place or type a new name"
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updatePlace(current, place.proposalId, (item) => ({ ...item, name: value }))
                      : current,
                  )
                }
                onSelect={(selected) =>
                  setDraft((current) =>
                    current
                      ? updatePlace(current, place.proposalId, (item) => ({
                          ...item,
                          reference: selected.place_id,
                          name: selected.name || selected.place_id,
                          address: [selected.address, selected.city, selected.country]
                            .filter(Boolean)
                            .join(', '),
                        }))
                      : current,
                  )
                }
              />
              <TextInput
                value={place.address}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updatePlace(current, place.proposalId, (item) => ({
                          ...item,
                          address: value,
                        }))
                      : current,
                  )
                }
                placeholder="Address"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
            </Card>
          ))}

          {draft.placeLinks.map((link) => (
            <Card key={link.proposalId} style={styles.card}>
              <SectionHeader
                title="Place link"
                subtitle={`${link.contactDisplayName || 'Contact'} -> ${link.placeName || 'Place'}`}
                onRemove={() =>
                  setDraft((current) =>
                    current
                      ? {
                          ...current,
                          placeLinks: current.placeLinks.filter(
                            (item) => item.proposalId !== link.proposalId,
                          ),
                        }
                      : current,
                  )
                }
              />
              <ContactPickerField
                value={link.contactDisplayName}
                query={link.contactDisplayName}
                contacts={session.availableContacts}
                placeholder="Search contact or type a name"
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updatePlaceLink(current, link.proposalId, (item) => ({
                          ...item,
                          contactDisplayName: value,
                        }))
                      : current,
                  )
                }
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
              <PlacePickerField
                value={link.placeName}
                query={link.placeName}
                places={session.availablePlaces}
                placeholder="Search place or type a name"
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updatePlaceLink(current, link.proposalId, (item) => ({
                          ...item,
                          placeName: value,
                        }))
                      : current,
                  )
                }
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
              <TextInput
                value={link.role}
                onChangeText={(value) =>
                  setDraft((current) =>
                    current
                      ? updatePlaceLink(current, link.proposalId, (item) => ({
                          ...item,
                          role: value,
                        }))
                      : current,
                  )
                }
                placeholder="Role"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
            </Card>
          ))}
        </Animated.ScrollView>

        <CollapsingTopBar
          title="Contact proposal"
          secondaryTitle="Edit draft"
          scrollY={scrollY}
          onPressBack={handleCancel}
        />

        <FloatingSaveButton
          visible
          label="Done"
          onPress={handleSave}
          bottomOffset={floatingOffset(insets.bottom, keyboardHeight)}
        />
      </KeyboardAvoidingView>

      {selectedBirthdayContact ? (
        <UiDirectiveDateTimePickerSheet
          visible
          mode="date"
          value={selectedBirthdayContact.birthday || undefined}
          onClose={() => setBirthdayPickerContactId(null)}
          onConfirm={(nextValue) => {
            setDraft((current) =>
              current
                ? updateContact(current, selectedBirthdayContact.proposalId, (item) => ({
                    ...item,
                    birthday: nextValue,
                  }))
                : current,
            );
            setBirthdayPickerContactId(null);
          }}
        />
      ) : null}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  screen: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    gap: 14,
  },
  subtitle: {
    marginTop: 8,
    fontSize: 14,
    lineHeight: 20,
    color: theme.colors.mutedInk,
  },
  card: {
    padding: 16,
    gap: 10,
  },
  label: {
    color: theme.colors.ink,
    fontSize: 13,
    fontWeight: '600',
  },
  helperText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  fieldLabel: {
    color: theme.colors.ink,
    fontSize: 13,
    fontWeight: '600',
  },
  input: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: theme.colors.ink,
    backgroundColor: '#fff',
    fontSize: 14,
  },
  textarea: {
    minHeight: 100,
    textAlignVertical: 'top',
  },
  dateInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  dateField: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    paddingHorizontal: 12,
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  dateFieldExpanded: {
    flex: 1,
  },
  dateFieldPressed: {
    borderColor: theme.colors.accent,
  },
  dateValue: {
    color: theme.colors.ink,
    fontSize: 14,
  },
  datePlaceholder: {
    color: theme.colors.mutedInk,
    fontSize: 14,
  },
  clearIconButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: theme.colors.line,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  clearIconButtonPressed: {
    opacity: 0.74,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  sectionTitleWrap: {
    flex: 1,
    gap: 2,
  },
  iconButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: theme.colors.line,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  iconButtonPressed: {
    opacity: 0.74,
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  switchLabel: {
    color: theme.colors.ink,
    fontSize: 14,
    fontWeight: '600',
  },
  suggestionList: {
    gap: 8,
  },
  selectedEntityRow: {
    minHeight: 48,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  selectedEntityTitle: {
    color: theme.colors.ink,
    fontSize: 14,
    fontWeight: '700',
  },
  suggestionRow: {
    minHeight: 44,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  suggestionPressed: {
    opacity: 0.74,
  },
  suggestionBody: {
    flex: 1,
  },
  suggestionText: {
    color: theme.colors.ink,
    fontSize: 14,
    fontWeight: '600',
  },
  suggestionMeta: {
    color: theme.colors.mutedInk,
    fontSize: 12,
    marginTop: 2,
  },
  emptyState: {
    flex: 1,
    paddingHorizontal: 24,
    alignItems: 'center',
  },
  emptyTitle: {
    color: theme.colors.ink,
    fontSize: 20,
    fontWeight: '800',
    marginBottom: 8,
  },
  emptyBody: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 20,
    textAlign: 'center',
  },
  emptyAction: {
    marginTop: 18,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  emptyActionText: {
    color: theme.colors.ink,
    fontSize: 14,
    fontWeight: '600',
  },
});
