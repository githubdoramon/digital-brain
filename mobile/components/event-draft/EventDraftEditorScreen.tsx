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
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Card } from '@/components/Card';
import { AppPressable as Pressable } from '@/components/AppPressable';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { UiDirectiveDateTimePickerSheet } from '@/components/ui-directive-card/UiDirectiveDateTimePickerSheet';
import {
  EMPTY_EVENT_DRAFT,
  type EventContactOption,
  type EventDraft,
  type EventPlaceOption,
} from '@/components/event-draft/types';
import {
  getEventDraftEditSession,
  submitEventDraftEditSession,
} from '@/events/draftEditorSession';
import { theme } from '@/theme';
import { normalizeSearch } from '@/utils/text';
import { matchesContactSearch } from '@/utils/contactSearch';

type EventDetailsFormProps = {
  initialDraft: EventDraft;
  availableContacts: EventContactOption[];
  availablePlaces: EventPlaceOption[];
  editable: boolean;
  headerKicker: string;
  headerTitle: string;
  headerSubtitle?: string;
  doneLabel?: string;
  onDone?: (draft: EventDraft) => void;
  onPressBack?: () => void;
};

type DraftEditorScreenProps = {
  sessionId: string;
};

function listToInput(value: string[]) {
  return value.join(', ');
}

function inputToList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatWhen(value: string) {
  if (!value.trim()) return 'Not specified';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function floatingOffset(insetBottom: number, keyboardHeight: number) {
  const keyboardInset =
    Platform.OS === 'ios' ? Math.max(0, keyboardHeight - insetBottom) : keyboardHeight;
  return insetBottom + 20 + keyboardInset;
}

function readOnlyText(value: string, fallback: string) {
  const trimmed = value.trim();
  return trimmed || fallback;
}

function readOnlyList(values: string[], fallback: string) {
  const filtered = values.map((value) => value.trim()).filter(Boolean);
  return filtered.length ? filtered : [fallback];
}

function formatPlaceLabel(place: EventPlaceOption): string {
  const parts = [place.name, place.city, place.country]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  if (parts.length > 0) return parts.join(', ');
  return place.place_id;
}

function buildPlaceSearchText(place: EventPlaceOption): string {
  return [place.name, place.address, place.city, place.country, ...(place.aliases || [])]
    .filter(Boolean)
    .join(' ');
}

export function EventDetailsForm({
  initialDraft,
  availableContacts,
  availablePlaces,
  editable,
  headerKicker,
  headerTitle,
  headerSubtitle,
  doneLabel = 'Done',
  onDone,
  onPressBack,
}: EventDetailsFormProps) {
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const [keyboardHeight, setKeyboardHeight] = React.useState(0);
  const [showDatePicker, setShowDatePicker] = React.useState(false);

  const [title, setTitle] = React.useState(initialDraft.title);
  const [summary, setSummary] = React.useState(initialDraft.summary);
  const [when, setWhen] = React.useState(initialDraft.when);
  const [where, setWhere] = React.useState(initialDraft.where);
  const [selectedPlaceId, setSelectedPlaceId] = React.useState<string | null>(
    initialDraft.placeId || null,
  );
  const [tagsInput, setTagsInput] = React.useState(listToInput(initialDraft.tags));
  const [typesInput, setTypesInput] = React.useState(listToInput(initialDraft.types));
  const [participantQuery, setParticipantQuery] = React.useState('');
  const [selectedParticipantIds, setSelectedParticipantIds] = React.useState<string[]>(
    initialDraft.participants.map((participant) => participant.contactId),
  );

  React.useEffect(() => {
    setTitle(initialDraft.title);
    setSummary(initialDraft.summary);
    setWhen(initialDraft.when);
    setWhere(initialDraft.where);
    setSelectedPlaceId(initialDraft.placeId || null);
    setTagsInput(listToInput(initialDraft.tags));
    setTypesInput(listToInput(initialDraft.types));
    setSelectedParticipantIds(initialDraft.participants.map((participant) => participant.contactId));
    setParticipantQuery('');
  }, [initialDraft]);

  React.useEffect(() => {
    if (!editable) {
      setKeyboardHeight(0);
      return;
    }

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
  }, [editable]);

  const initialParticipants = React.useMemo(
    () => initialDraft.participants ?? [],
    [initialDraft.participants],
  );

  const contactNameById = React.useMemo(() => {
    const lookup = new Map<string, string>();
    for (const contact of availableContacts) {
      lookup.set(contact.contact_id, contact.display_name);
    }
    for (const participant of initialParticipants) {
      if (!lookup.has(participant.contactId)) {
        lookup.set(participant.contactId, participant.displayName);
      }
    }
    return lookup;
  }, [availableContacts, initialParticipants]);

  const selectedParticipants = React.useMemo(
    () =>
      selectedParticipantIds.map((contactId) => ({
        contactId,
        displayName: contactNameById.get(contactId) || contactId,
      })),
    [contactNameById, selectedParticipantIds],
  );

  const filteredContacts = React.useMemo(() => {
    if (!editable) return [];
    const query = participantQuery.trim();
    if (!query) return [];
    const selectedSet = new Set(selectedParticipantIds);
    return availableContacts
      .filter((contact) => !selectedSet.has(contact.contact_id))
      .filter((contact) => matchesContactSearch(contact, query))
      .slice(0, 5);
  }, [availableContacts, editable, participantQuery, selectedParticipantIds]);

  const filteredPlaces = React.useMemo(() => {
    if (!editable) return [];
    const query = normalizeSearch(where.trim());
    if (!query) return [];
    return availablePlaces
      .filter((place) => normalizeSearch(buildPlaceSearchText(place)).includes(query))
      .slice(0, 6);
  }, [availablePlaces, editable, where]);

  const toggleParticipant = React.useCallback((contactId: string) => {
    setSelectedParticipantIds((prev) =>
      prev.includes(contactId) ? prev.filter((id) => id !== contactId) : [...prev, contactId],
    );
    setParticipantQuery('');
  }, []);

  const currentDraft: EventDraft = React.useMemo(
    () => ({
      title: title.trim(),
      summary: summary.trim(),
      when: when.trim(),
      where: where.trim(),
      placeId: selectedPlaceId,
      tags: inputToList(tagsInput),
      types: inputToList(typesInput),
      participants: selectedParticipants,
    }),
    [selectedParticipants, selectedPlaceId, summary, tagsInput, title, typesInput, when, where],
  );

  const readOnlyParticipants = selectedParticipants;
  const readOnlyTags = readOnlyList(inputToList(tagsInput), 'None');
  const readOnlyTypes = readOnlyList(inputToList(typesInput), 'Generic');

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
              paddingBottom: insets.bottom + (editable ? 120 : 28),
            },
          ]}
        >
          {headerSubtitle ? <Text style={styles.subtitle}>{headerSubtitle}</Text> : null}

          <Card style={styles.card}>
            <Text style={styles.label}>Title</Text>
            {editable ? (
              <TextInput
                value={title}
                onChangeText={setTitle}
                placeholder="Add a short title"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
            ) : (
              <Text style={styles.readText}>{readOnlyText(title, 'Untitled event')}</Text>
            )}
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Summary</Text>
            {editable ? (
              <TextInput
                value={summary}
                onChangeText={setSummary}
                placeholder="Capture what happened"
                placeholderTextColor={theme.colors.mutedInk}
                multiline
                style={[styles.input, styles.textarea]}
              />
            ) : (
              <Text style={styles.readText}>{readOnlyText(summary, 'No summary provided.')}</Text>
            )}
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>When</Text>
            {editable ? (
              <>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Select event time"
                  onPress={() => setShowDatePicker(true)}
                  style={({ pressed }) => [styles.dateField, pressed && styles.dateFieldPressed]}
                >
                  <Text style={when ? styles.dateValue : styles.datePlaceholder}>
                    {when ? formatWhen(when) : 'Pick date and time'}
                  </Text>
                </Pressable>
                {when ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Clear event time"
                    onPress={() => setWhen('')}
                    style={({ pressed }) => [styles.clearLink, pressed && styles.clearLinkPressed]}
                  >
                    <Text style={styles.clearLinkText}>Clear time</Text>
                  </Pressable>
                ) : null}
              </>
            ) : (
              <Text style={styles.readText}>{formatWhen(when)}</Text>
            )}
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Where</Text>
            {editable ? (
              <>
                <TextInput
                  value={where}
                  onChangeText={(value) => {
                    setWhere(value);
                    setSelectedPlaceId(null);
                  }}
                  placeholder="Search places"
                  placeholderTextColor={theme.colors.mutedInk}
                  style={styles.input}
                />
                {selectedPlaceId ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Clear linked place"
                    onPress={() => setSelectedPlaceId(null)}
                    style={({ pressed }) => [styles.clearLink, pressed && styles.clearLinkPressed]}
                  >
                    <Text style={styles.clearLinkText}>Clear linked place</Text>
                  </Pressable>
                ) : null}
                {where.trim().length > 0 && filteredPlaces.length > 0 ? (
                  <View style={styles.suggestionList}>
                    {filteredPlaces.map((place) => (
                      <Pressable
                        key={place.place_id}
                        accessibilityRole="button"
                        accessibilityLabel={`Select ${formatPlaceLabel(place)}`}
                        onPress={() => {
                          setWhere(formatPlaceLabel(place));
                          setSelectedPlaceId(place.place_id);
                        }}
                        style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
                      >
                        <View style={styles.suggestionBody}>
                          <Text style={styles.suggestionText}>{formatPlaceLabel(place)}</Text>
                          {place.address ? (
                            <Text style={styles.suggestionMeta}>{place.address}</Text>
                          ) : null}
                        </View>
                        <Ionicons name="location-outline" size={16} color={theme.colors.accentDeep} />
                      </Pressable>
                    ))}
                  </View>
                ) : null}
              </>
            ) : (
              <Text style={styles.readText}>{readOnlyText(where, 'Not specified')}</Text>
            )}
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Participants</Text>
            {editable ? (
              <>
                <TextInput
                  value={participantQuery}
                  onChangeText={setParticipantQuery}
                  placeholder="Search contacts"
                  placeholderTextColor={theme.colors.mutedInk}
                  style={styles.input}
                />

                {selectedParticipants.length > 0 ? (
                  <View style={styles.chipRow}>
                    {selectedParticipants.map((participant) => (
                      <Pressable
                        key={participant.contactId}
                        accessibilityRole="button"
                        accessibilityLabel={`Remove ${participant.displayName}`}
                        onPress={() => toggleParticipant(participant.contactId)}
                        style={({ pressed }) => [styles.chip, pressed && styles.chipPressed]}
                      >
                        <Text style={styles.chipText}>{participant.displayName}</Text>
                        <Ionicons name="close" size={12} color={theme.colors.mutedInk} />
                      </Pressable>
                    ))}
                  </View>
                ) : (
                  <Text style={styles.helperText}>No participants selected.</Text>
                )}

                {participantQuery.trim().length > 0 && filteredContacts.length > 0 ? (
                  <View style={styles.suggestionList}>
                    {filteredContacts.map((contact) => (
                      <Pressable
                        key={contact.contact_id}
                        accessibilityRole="button"
                        accessibilityLabel={`Add ${contact.display_name}`}
                        onPress={() => toggleParticipant(contact.contact_id)}
                        style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
                      >
                        <Text style={styles.suggestionText}>{contact.display_name}</Text>
                        <Ionicons name="add" size={16} color={theme.colors.accentDeep} />
                      </Pressable>
                    ))}
                  </View>
                ) : null}
              </>
            ) : readOnlyParticipants.length > 0 ? (
              <View style={styles.chipRow}>
                {readOnlyParticipants.map((participant) => (
                  <View key={participant.contactId} style={styles.readChip}>
                    <Text style={styles.chipText}>{participant.displayName}</Text>
                  </View>
                ))}
              </View>
            ) : (
              <Text style={styles.readText}>No participants detected</Text>
            )}
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Tags</Text>
            {editable ? (
              <TextInput
                value={tagsInput}
                onChangeText={setTagsInput}
                placeholder="work, meeting, personal"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
            ) : (
              <View style={styles.chipRow}>
                {readOnlyTags.map((tag) => (
                  <View key={`tag:${tag}`} style={styles.readChip}>
                    <Text style={styles.chipText}>{tag}</Text>
                  </View>
                ))}
              </View>
            )}
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Types</Text>
            {editable ? (
              <TextInput
                value={typesInput}
                onChangeText={setTypesInput}
                placeholder="meeting, travel, personal"
                placeholderTextColor={theme.colors.mutedInk}
                style={styles.input}
              />
            ) : (
              <View style={styles.chipRow}>
                {readOnlyTypes.map((typeValue) => (
                  <View key={`type:${typeValue}`} style={styles.readChip}>
                    <Text style={styles.chipText}>{typeValue}</Text>
                  </View>
                ))}
              </View>
            )}
          </Card>
        </Animated.ScrollView>

        <CollapsingTopBar
          title={headerKicker}
          secondaryTitle={headerTitle}
          scrollY={scrollY}
          onPressBack={onPressBack}
        />

        {editable ? (
          <FloatingSaveButton
            visible
            label={doneLabel}
            onPress={() => onDone?.(currentDraft)}
            disabled={!onDone}
            bottomOffset={floatingOffset(insets.bottom, keyboardHeight)}
          />
        ) : null}
      </KeyboardAvoidingView>

      {editable && showDatePicker ? (
        <UiDirectiveDateTimePickerSheet
          visible
          mode="datetime"
          value={when || undefined}
          onClose={() => setShowDatePicker(false)}
          onConfirm={(nextValue) => {
            setWhen(nextValue);
            setShowDatePicker(false);
          }}
        />
      ) : null}
    </LinearGradient>
  );
}

export function EventDraftEditorScreen({ sessionId }: DraftEditorScreenProps) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const session = React.useMemo(() => getEventDraftEditSession(sessionId), [sessionId]);

  const handleDone = React.useCallback(
    (nextDraft: EventDraft) => {
      console.info('[event-draft-session] editor-done-pressed', {
        sessionId,
        hasSession: Boolean(session),
      });
      if (session) {
        submitEventDraftEditSession(session.sessionId, nextDraft);
      }
      router.back();
    },
    [router, session, sessionId],
  );

  React.useEffect(() => {
    console.info('[event-draft-session] editor-screen-mounted', {
      sessionId,
      hasSession: Boolean(session),
    });
  }, [sessionId, session]);

  if (!session) {
    return (
      <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
        <View style={[styles.emptyState, { paddingTop: insets.top + 80 }]}> 
          <Text style={styles.emptyTitle}>Draft editor unavailable</Text>
          <Text style={styles.emptyBody}>This draft has expired. Return to chat and re-open edit.</Text>
          <Pressable onPress={() => router.back()} style={styles.emptyAction}>
            <Text style={styles.emptyActionText}>Back to chat</Text>
          </Pressable>
        </View>
      </LinearGradient>
    );
  }

  return (
    <EventDetailsForm
      initialDraft={session.initialDraft || EMPTY_EVENT_DRAFT}
      availableContacts={session.availableContacts}
      availablePlaces={[]}
      editable
      headerKicker="Event proposal"
      headerTitle="Edit draft"
      headerSubtitle="Review details before creating the event."
      doneLabel="Done"
      onDone={handleDone}
      onPressBack={() => router.back()}
    />
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
  dateField: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    paddingHorizontal: 12,
    justifyContent: 'center',
    backgroundColor: '#fff',
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
  clearLink: {
    alignSelf: 'flex-start',
    minHeight: 32,
    justifyContent: 'center',
    paddingHorizontal: 2,
  },
  clearLinkPressed: {
    opacity: 0.74,
  },
  clearLinkText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.accentDeep,
  },
  helperText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  suggestionList: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    overflow: 'hidden',
  },
  suggestionRow: {
    minHeight: 44,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#fff',
  },
  suggestionPressed: {
    backgroundColor: '#f7f2ec',
  },
  suggestionText: {
    color: theme.colors.ink,
    fontSize: 14,
    lineHeight: 19,
    flexShrink: 1,
    marginRight: 10,
  },
  suggestionBody: {
    flex: 1,
    marginRight: 10,
  },
  suggestionMeta: {
    color: theme.colors.mutedInk,
    fontSize: 12,
    lineHeight: 16,
    marginTop: 2,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    minHeight: 34,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 10,
  },
  readChip: {
    minHeight: 32,
    justifyContent: 'center',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 10,
  },
  chipPressed: {
    opacity: 0.78,
  },
  chipText: {
    color: theme.colors.ink,
    fontSize: 12,
    lineHeight: 16,
    fontWeight: '600',
  },
  readText: {
    color: theme.colors.ink,
    fontSize: 14,
    lineHeight: 20,
  },
  emptyState: {
    flex: 1,
    paddingHorizontal: 24,
    gap: 12,
  },
  emptyTitle: {
    fontSize: 24,
    lineHeight: 30,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  emptyBody: {
    fontSize: 14,
    lineHeight: 21,
    color: theme.colors.mutedInk,
  },
  emptyAction: {
    alignSelf: 'flex-start',
    minHeight: 40,
    borderRadius: 20,
    paddingHorizontal: 14,
    justifyContent: 'center',
    backgroundColor: theme.colors.ink,
    marginTop: 6,
  },
  emptyActionText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 13,
  },
});
