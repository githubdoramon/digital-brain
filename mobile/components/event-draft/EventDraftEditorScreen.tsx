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
import { Button } from '@/components/Button';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { EventPhotoCard } from '@/components/event-draft/EventPhotoCard';
import { UiDirectiveDateTimePickerSheet } from '@/components/ui-directive-card/UiDirectiveDateTimePickerSheet';
import {
  EMPTY_EVENT_DRAFT,
  type EventContactOption,
  type EventDraft,
  type EventMatchCandidate,
  type EventPhoto,
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
  candidateEvents?: EventMatchCandidate[];
  editable: boolean;
  headerKicker: string;
  headerTitle: string;
  headerSubtitle?: string;
  doneLabel?: string;
  deleteLabel?: string;
  photos?: EventPhoto[];
  photoToken?: string | null;
  isUploadingPhoto?: boolean;
  onAddPhoto?: () => void;
  onRemovePhoto?: (assetId: string) => void;
  onDelete?: () => void;
  deleteDisabled?: boolean;
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

function formatCandidateWhen(value: string | null): string {
  if (!value) return 'No date';
  return formatWhen(value);
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
  candidateEvents = [],
  editable,
  headerKicker,
  headerTitle,
  headerSubtitle,
  doneLabel = 'Done',
  deleteLabel = 'Delete',
  photos = [],
  photoToken,
  isUploadingPhoto = false,
  onAddPhoto,
  onRemovePhoto,
  onDelete,
  deleteDisabled = false,
  onDone,
  onPressBack,
}: EventDetailsFormProps) {
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const [keyboardHeight, setKeyboardHeight] = React.useState(0);
  const [showWhenPicker, setShowWhenPicker] = React.useState(false);
  const [showEndDateTimePicker, setShowEndDateTimePicker] = React.useState(false);

  const [title, setTitle] = React.useState(initialDraft.title);
  const [summary, setSummary] = React.useState(initialDraft.summary);
  const [when, setWhen] = React.useState(initialDraft.when || '');
  const [endWhen, setEndWhen] = React.useState(initialDraft.endWhen || '');
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
  const [operation, setOperation] = React.useState(initialDraft.operation);
  const [existingEventId, setExistingEventId] = React.useState<string | null>(
    initialDraft.existingEventId,
  );
  const [matchedEvent, setMatchedEvent] = React.useState<EventMatchCandidate | null>(
    initialDraft.matchedEvent,
  );
  const [showCandidatePicker, setShowCandidatePicker] = React.useState(false);

  React.useEffect(() => {
    setTitle(initialDraft.title);
    setSummary(initialDraft.summary);
    setWhen(initialDraft.when || '');
    setEndWhen(initialDraft.endWhen || '');
    setWhere(initialDraft.where);
    setSelectedPlaceId(initialDraft.placeId || null);
    setTagsInput(listToInput(initialDraft.tags));
    setTypesInput(listToInput(initialDraft.types));
    setSelectedParticipantIds(initialDraft.participants.map((participant) => participant.contactId));
    setParticipantQuery('');
    setOperation(initialDraft.operation);
    setExistingEventId(initialDraft.existingEventId);
    setMatchedEvent(initialDraft.matchedEvent);
    setShowCandidatePicker(false);
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
      endWhen: endWhen.trim(),
      where: where.trim(),
      placeId: selectedPlaceId,
      tags: inputToList(tagsInput),
      types: inputToList(typesInput),
      participants: selectedParticipants,
      operation: operation === 'update' && existingEventId ? 'update' : 'create',
      existingEventId: operation === 'update' ? existingEventId : null,
      matchedEvent: operation === 'update' ? matchedEvent : null,
    }),
    [
      endWhen,
      existingEventId,
      matchedEvent,
      operation,
      selectedParticipants,
      selectedPlaceId,
      summary,
      tagsInput,
      title,
      typesInput,
      when,
      where,
    ],
  );

  const availableCandidates = React.useMemo(() => {
    const seen = new Set<string>();
    const combined: EventMatchCandidate[] = [];
    if (matchedEvent) {
      combined.push(matchedEvent);
      seen.add(matchedEvent.eventId);
    }
    for (const candidate of candidateEvents) {
      if (seen.has(candidate.eventId)) continue;
      seen.add(candidate.eventId);
      combined.push(candidate);
    }
    return combined;
  }, [candidateEvents, matchedEvent]);

  const selectCandidate = React.useCallback((candidate: EventMatchCandidate) => {
    setOperation('update');
    setExistingEventId(candidate.eventId);
    setMatchedEvent(candidate);
    setShowCandidatePicker(false);
  }, []);

  const createNewInstead = React.useCallback(() => {
    setOperation('create');
    setExistingEventId(null);
    setMatchedEvent(null);
    setShowCandidatePicker(false);
  }, []);

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

          {editable && operation === 'update' && matchedEvent ? (
            <Card style={[styles.card, styles.matchCard]}>
              <View style={styles.matchHeaderRow}>
                <Ionicons name="git-merge-outline" size={16} color={theme.colors.accentDeep} />
                <Text style={styles.matchKicker}>Updating existing event</Text>
              </View>
              <Text style={styles.matchTitle}>{matchedEvent.title || 'Untitled event'}</Text>
              <Text style={styles.matchMeta}>{formatCandidateWhen(matchedEvent.startDate)}</Text>
              {matchedEvent.place?.name ? (
                <Text style={styles.matchMeta}>{matchedEvent.place.name}</Text>
              ) : null}
              {matchedEvent.matchScore > 0 ? (
                <Text style={styles.matchMeta}>
                  Match confidence: {Math.round(matchedEvent.matchScore)}%
                </Text>
              ) : null}
              <View style={styles.matchActions}>
                {availableCandidates.length > 1 ? (
                  <Button
                    label={showCandidatePicker ? 'Hide alternatives' : 'Pick a different event'}
                    variant="secondary"
                    onPress={() => setShowCandidatePicker((prev) => !prev)}
                  />
                ) : null}
                <Button label="Create new instead" variant="secondary" onPress={createNewInstead} />
              </View>
              {showCandidatePicker ? (
                <View style={styles.candidateList}>
                  {availableCandidates.map((candidate) => {
                    const isSelected = candidate.eventId === existingEventId;
                    return (
                      <Pressable
                        key={candidate.eventId}
                        accessibilityRole="button"
                        accessibilityLabel={`Select ${candidate.title || 'event'}`}
                        onPress={() => selectCandidate(candidate)}
                        style={({ pressed }) => [
                          styles.candidateRow,
                          isSelected && styles.candidateRowSelected,
                          pressed && styles.candidateRowPressed,
                        ]}
                      >
                        <View style={styles.candidateBody}>
                          <Text style={styles.candidateTitle}>
                            {candidate.title || 'Untitled event'}
                          </Text>
                          <Text style={styles.candidateMeta}>
                            {formatCandidateWhen(candidate.startDate)}
                          </Text>
                          {candidate.place?.name ? (
                            <Text style={styles.candidateMeta}>{candidate.place.name}</Text>
                          ) : null}
                        </View>
                        <Text style={styles.candidateScore}>
                          {candidate.matchScore > 0
                            ? `${Math.round(candidate.matchScore)}%`
                            : ''}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              ) : null}
            </Card>
          ) : null}

          {editable && operation === 'create' && availableCandidates.length > 0 ? (
            <Card style={[styles.card, styles.matchCard]}>
              <View style={styles.matchHeaderRow}>
                <Ionicons name="search-outline" size={16} color={theme.colors.accentDeep} />
                <Text style={styles.matchKicker}>Similar existing events</Text>
              </View>
              <Text style={styles.matchMeta}>
                Tap one to update it instead of creating a new event.
              </Text>
              <View style={styles.candidateList}>
                {availableCandidates.map((candidate) => (
                  <Pressable
                    key={candidate.eventId}
                    accessibilityRole="button"
                    accessibilityLabel={`Update ${candidate.title || 'event'} instead`}
                    onPress={() => selectCandidate(candidate)}
                    style={({ pressed }) => [
                      styles.candidateRow,
                      pressed && styles.candidateRowPressed,
                    ]}
                  >
                    <View style={styles.candidateBody}>
                      <Text style={styles.candidateTitle}>
                        {candidate.title || 'Untitled event'}
                      </Text>
                      <Text style={styles.candidateMeta}>
                        {formatCandidateWhen(candidate.startDate)}
                      </Text>
                      {candidate.place?.name ? (
                        <Text style={styles.candidateMeta}>{candidate.place.name}</Text>
                      ) : null}
                    </View>
                    <Text style={styles.candidateScore}>
                      {candidate.matchScore > 0
                        ? `${Math.round(candidate.matchScore)}%`
                        : ''}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </Card>
          ) : null}

          {photos.length > 0 || onAddPhoto || onRemovePhoto ? (
            <Card style={styles.card}>
              <EventPhotoCard
                photos={photos}
                editable={Boolean(onAddPhoto) || Boolean(onRemovePhoto)}
                isUploading={isUploadingPhoto}
                token={photoToken}
                onAddPhoto={onAddPhoto}
                onRemovePhoto={onRemovePhoto}
              />
            </Card>
          ) : null}

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
                <Text style={styles.helperText}>Start</Text>
                <View style={styles.dateInputRow}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Select event start date and time"
                    onPress={() => setShowWhenPicker(true)}
                    style={({ pressed }) => [styles.dateField, styles.dateFieldExpanded, pressed && styles.dateFieldPressed]}
                  >
                    <Text style={when ? styles.dateValue : styles.datePlaceholder}>
                      {when ? formatWhen(when) : 'Add start date and time'}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Clear event start date and time"
                    onPress={() => setWhen('')}
                    style={({ pressed }) => [styles.clearIconButton, pressed && styles.clearIconButtonPressed]}
                  >
                    <Ionicons name="close" size={16} color={theme.colors.mutedInk} />
                  </Pressable>
                </View>

                <Text style={styles.helperText}>End (optional)</Text>
                <View style={styles.dateInputRow}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Select event end date and time"
                    onPress={() => setShowEndDateTimePicker(true)}
                    style={({ pressed }) => [styles.dateField, styles.dateFieldExpanded, pressed && styles.dateFieldPressed]}
                  >
                    <Text style={endWhen ? styles.dateValue : styles.datePlaceholder}>
                      {endWhen ? formatWhen(endWhen) : 'Add end date and time'}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Clear event end date and time"
                    onPress={() => setEndWhen('')}
                    style={({ pressed }) => [styles.clearIconButton, pressed && styles.clearIconButtonPressed]}
                  >
                    <Ionicons name="close" size={16} color={theme.colors.mutedInk} />
                  </Pressable>
                </View>
              </>
            ) : (
              <>
                <Text style={styles.readText}>{`Start: ${formatWhen(when)}`}</Text>
                <Text style={styles.readText}>{`End: ${formatWhen(endWhen)}`}</Text>
              </>
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

          {!editable && onDelete ? (
            <Card style={styles.deleteSection}>
              <Text style={styles.label}>Danger zone</Text>
              <Text style={styles.deleteHint}>
                This permanently deletes this event and unlinks related todos.
              </Text>
              <Button
                label={deleteLabel}
                variant="danger"
                onPress={onDelete}
                disabled={deleteDisabled}
              />
            </Card>
          ) : null}
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

      {editable && showWhenPicker ? (
        <UiDirectiveDateTimePickerSheet
          visible
          mode="datetime"
          value={when || undefined}
          onClose={() => setShowWhenPicker(false)}
          onConfirm={(nextValue) => {
            setWhen(nextValue);
            setShowWhenPicker(false);
          }}
        />
      ) : null}

      {editable && showEndDateTimePicker ? (
        <UiDirectiveDateTimePickerSheet
          visible
          mode="datetime"
          value={endWhen || undefined}
          onClose={() => setShowEndDateTimePicker(false)}
          onConfirm={(nextValue) => {
            setEndWhen(nextValue);
            setShowEndDateTimePicker(false);
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
      availablePlaces={session.availablePlaces}
      candidateEvents={session.candidateEvents}
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
  dateFieldExpanded: {
    flex: 1,
  },
  dateInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
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
  deleteSection: {
    padding: 16,
    gap: 10,
  },
  deleteHint: {
    fontSize: 13,
    lineHeight: 18,
    color: theme.colors.mutedInk,
  },
  matchCard: {
    borderWidth: 1,
    borderColor: theme.colors.accent,
    backgroundColor: '#fffaf2',
  },
  matchHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  matchKicker: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
    color: theme.colors.accentDeep,
  },
  matchTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  matchMeta: {
    fontSize: 13,
    lineHeight: 18,
    color: theme.colors.mutedInk,
  },
  matchActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 4,
  },
  candidateList: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    overflow: 'hidden',
    marginTop: 6,
  },
  candidateRow: {
    minHeight: 56,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#fff',
  },
  candidateRowSelected: {
    backgroundColor: '#fdf1df',
  },
  candidateRowPressed: {
    backgroundColor: '#f7f2ec',
  },
  candidateBody: {
    flex: 1,
    marginRight: 10,
    gap: 2,
  },
  candidateTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  candidateMeta: {
    fontSize: 12,
    lineHeight: 16,
    color: theme.colors.mutedInk,
  },
  candidateScore: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.accentDeep,
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
