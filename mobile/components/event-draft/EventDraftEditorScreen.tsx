import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { UiDirectiveDateTimePickerSheet } from '@/components/ui-directive-card/UiDirectiveDateTimePickerSheet';
import type { EventContactOption, EventDraft } from '@/components/event-draft/types';
import {
  getEventDraftEditSession,
  submitEventDraftEditSession,
} from '@/events/draftEditorSession';
import { theme } from '@/theme';

type Props = {
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
  if (!value.trim()) return 'No time selected';
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
  const keyboardInset = Platform.OS === 'ios' ? Math.max(0, keyboardHeight - insetBottom) : keyboardHeight;
  return insetBottom + 20 + keyboardInset;
}

export function EventDraftEditorScreen({ sessionId }: Props) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const session = React.useMemo(() => getEventDraftEditSession(sessionId), [sessionId]);
  const [keyboardHeight, setKeyboardHeight] = React.useState(0);
  const [showDatePicker, setShowDatePicker] = React.useState(false);

  const [title, setTitle] = React.useState(session?.initialDraft.title ?? '');
  const [summary, setSummary] = React.useState(session?.initialDraft.summary ?? '');
  const [when, setWhen] = React.useState(session?.initialDraft.when ?? '');
  const [where, setWhere] = React.useState(session?.initialDraft.where ?? '');
  const [tagsInput, setTagsInput] = React.useState(listToInput(session?.initialDraft.tags ?? []));
  const [typesInput, setTypesInput] = React.useState(listToInput(session?.initialDraft.types ?? []));
  const [participantQuery, setParticipantQuery] = React.useState('');
  const [selectedParticipantIds, setSelectedParticipantIds] = React.useState<string[]>(
    (session?.initialDraft.participants ?? []).map((participant) => participant.contactId),
  );

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

  const availableContacts: EventContactOption[] = React.useMemo(
    () => session?.availableContacts ?? [],
    [session?.availableContacts],
  );
  const initialParticipants = React.useMemo(
    () => session?.initialDraft.participants ?? [],
    [session?.initialDraft.participants],
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
    const query = participantQuery.trim().toLowerCase();
    if (!query) return [];
    const selectedSet = new Set(selectedParticipantIds);
    return availableContacts
      .filter((contact) => !selectedSet.has(contact.contact_id))
      .filter((contact) => contact.display_name.toLowerCase().includes(query))
      .slice(0, 5);
  }, [availableContacts, participantQuery, selectedParticipantIds]);

  const toggleParticipant = React.useCallback((contactId: string) => {
    setSelectedParticipantIds((prev) =>
      prev.includes(contactId) ? prev.filter((id) => id !== contactId) : [...prev, contactId],
    );
    setParticipantQuery('');
  }, []);

  const handleDone = React.useCallback(() => {
    if (session) {
      const nextDraft: EventDraft = {
        title: title.trim(),
        summary: summary.trim(),
        when: when.trim(),
        where: where.trim(),
        tags: inputToList(tagsInput),
        types: inputToList(typesInput),
        participants: selectedParticipants,
      };
      submitEventDraftEditSession(session.sessionId, nextDraft);
    }
    router.back();
  }, [router, selectedParticipants, session, summary, tagsInput, title, typesInput, when, where]);

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
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 72 : 0}
      >
        <ScrollView
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[
            styles.content,
            {
              paddingTop: insets.top + 62,
              paddingBottom: insets.bottom + 120,
            },
          ]}
        >
          <Text style={styles.kicker}>Event proposal</Text>
          <Text style={styles.title}>Edit draft</Text>
          <Text style={styles.subtitle}>Review details before creating the event.</Text>

          <Card style={styles.card}>
            <Text style={styles.label}>Title</Text>
            <TextInput
              value={title}
              onChangeText={setTitle}
              placeholder="Add a short title"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.input}
            />
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Summary</Text>
            <TextInput
              value={summary}
              onChangeText={setSummary}
              placeholder="Capture what happened"
              placeholderTextColor={theme.colors.mutedInk}
              multiline
              style={[styles.input, styles.textarea]}
            />
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>When</Text>
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
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Where</Text>
            <TextInput
              value={where}
              onChangeText={setWhere}
              placeholder="Location"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.input}
            />
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Participants</Text>
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
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Tags</Text>
            <TextInput
              value={tagsInput}
              onChangeText={setTagsInput}
              placeholder="work, meeting, personal"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.input}
            />
          </Card>

          <Card style={styles.card}>
            <Text style={styles.label}>Types</Text>
            <TextInput
              value={typesInput}
              onChangeText={setTypesInput}
              placeholder="meeting, travel, personal"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.input}
            />
          </Card>
        </ScrollView>

        <FloatingSaveButton
          visible
          label="Done"
          onPress={handleDone}
          bottomOffset={floatingOffset(insets.bottom, keyboardHeight)}
        />
      </KeyboardAvoidingView>

      {showDatePicker ? (
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
  kicker: {
    fontSize: 12,
    letterSpacing: 2.6,
    textTransform: 'uppercase',
    color: theme.colors.accentDeep,
    fontWeight: '600',
  },
  title: {
    marginTop: 6,
    fontSize: 28,
    lineHeight: 34,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    marginTop: 4,
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
  chipPressed: {
    opacity: 0.78,
  },
  chipText: {
    color: theme.colors.ink,
    fontSize: 12,
    lineHeight: 16,
    fontWeight: '600',
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
