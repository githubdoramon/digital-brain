import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React from 'react';
import DateTimePicker, { DateType, useDefaultStyles } from 'react-native-ui-datepicker';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { TopNoticeProvider, useTopNotice } from '@/components/top-notice';
import { theme } from '@/theme';

const dueDatePattern = /^\d{4}-\d{2}-\d{2}$/;

type Contact = {
  contact_id: string;
  display_name: string;
};

type EventResult = {
  id: string;
  title?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

type TodoDetail = {
  todo_id: string;
  description?: string | null;
  status?: string | null;
  due_date?: string | null;
  contacts?: string[];
  events?: EventResult[];
};

const normalizeSearch = (value: string) =>
  value
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase();

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

function createTodoId() {
  return `todo_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
}

function NewTodoContent() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const params = useLocalSearchParams<{ todoId?: string | string[] }>();
  const { showNotice } = useTopNotice();
  const todoIdParam = React.useMemo(() => {
    if (Array.isArray(params.todoId)) {
      return params.todoId[0] || '';
    }
    return params.todoId || '';
  }, [params.todoId]);
  const isEditing = todoIdParam.trim().length > 0;
  const [description, setDescription] = React.useState('');
  const [dueDate, setDueDate] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [loadingTodo, setLoadingTodo] = React.useState(false);
  const [todoStatus, setTodoStatus] = React.useState('pending');
  const [contacts, setContacts] = React.useState<Contact[]>([]);
  const [contactQuery, setContactQuery] = React.useState('');
  const [selectedContacts, setSelectedContacts] = React.useState<Contact[]>([]);
  const [eventQuery, setEventQuery] = React.useState('');
  const [eventResults, setEventResults] = React.useState<EventResult[]>([]);
  const [selectedEvents, setSelectedEvents] = React.useState<EventResult[]>([]);
  const [eventsLoading, setEventsLoading] = React.useState(false);
  const [showDatePicker, setShowDatePicker] = React.useState(false);
  const [draftDate, setDraftDate] = React.useState<Date | null>(null);
  const defaultPickerStyles = useDefaultStyles();

  const trimmedDescription = description.trim();
  const trimmedDueDate = dueDate.trim();
  const canSave = trimmedDescription.length > 0 && !saving && !loadingTodo;
  const selectedContactIds = React.useMemo(
    () => new Set(selectedContacts.map((contact) => contact.contact_id)),
    [selectedContacts]
  );
  const selectedEventIds = React.useMemo(
    () => new Set(selectedEvents.map((event) => event.id)),
    [selectedEvents]
  );
  const quickDates = React.useMemo(() => {
    const today = new Date();
    const tomorrow = new Date();
    tomorrow.setDate(today.getDate() + 1);
    const nextWeek = new Date();
    nextWeek.setDate(today.getDate() + 7);
    return [
      { label: 'Today', value: formatIsoDate(today) },
      { label: 'Tomorrow', value: formatIsoDate(tomorrow) },
      { label: 'Next week', value: formatIsoDate(nextWeek) },
    ];
  }, []);
  const pickerDate = React.useMemo(() => {
    if (!trimmedDueDate) return new Date();
    const parsed = new Date(`${trimmedDueDate}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
  }, [trimmedDueDate]);
  const activePickerDate = draftDate ?? pickerDate;

  const filteredContacts = React.useMemo(() => {
    const trimmed = normalizeSearch(contactQuery.trim());
    if (!trimmed) return [];
    return contacts
      .filter((contact) =>
        normalizeSearch(contact.display_name).includes(trimmed)
      )
      .filter((contact) => !selectedContactIds.has(contact.contact_id))
      .slice(0, 6);
  }, [contactQuery, contacts, selectedContactIds]);

  React.useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        if (mounted) {
          setContacts(result.contacts ?? []);
        }
      } catch {
        if (mounted) {
          setContacts([]);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  React.useEffect(() => {
    if (!isEditing) {
      setLoadingTodo(false);
      setTodoStatus('pending');
      return;
    }
    let mounted = true;
    (async () => {
      setLoadingTodo(true);
      try {
        const result = (await apiFetch(`/mobile/todos/${encodeURIComponent(todoIdParam)}`)) as TodoDetail;
        if (!mounted) return;
        setDescription(result.description?.trim() || '');
        setDueDate(result.due_date || '');
        setTodoStatus(result.status?.trim() || 'pending');
        setSelectedContacts((result.contacts ?? []).map((contactId) => ({
          contact_id: contactId,
          display_name: contactId,
        })));
        setSelectedEvents((result.events ?? []).map((event) => ({
          id: event.id,
          title: event.title,
          start_date: event.start_date,
          end_date: event.end_date,
        })));
      } catch {
        if (!mounted) return;
        showNotice('Unable to load todo.', 'error');
      } finally {
        if (mounted) {
          setLoadingTodo(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [isEditing, showNotice, todoIdParam]);

  React.useEffect(() => {
    if (contacts.length === 0) return;
    setSelectedContacts((prev) =>
      prev.map((contact) => {
        const match = contacts.find((item) => item.contact_id === contact.contact_id);
        return match ? { contact_id: match.contact_id, display_name: match.display_name } : contact;
      })
    );
  }, [contacts]);

  React.useEffect(() => {
    const trimmed = eventQuery.trim();
    if (trimmed.length < 2) {
      setEventResults([]);
      setEventsLoading(false);
      return;
    }
    setEventsLoading(true);
    const handle = setTimeout(async () => {
      try {
        const result = (await apiFetch(
          `/mobile/events/search?query=${encodeURIComponent(trimmed)}`
        )) as { events: EventResult[] };
        setEventResults((result.events ?? []).filter((event) => !selectedEventIds.has(event.id)));
      } catch {
        setEventResults([]);
      } finally {
        setEventsLoading(false);
      }
    }, 250);

    return () => {
      clearTimeout(handle);
    };
  }, [eventQuery, selectedEventIds]);

  const handleAddContact = React.useCallback((contact: Contact) => {
    setSelectedContacts((prev) => {
      if (prev.some((item) => item.contact_id === contact.contact_id)) return prev;
      return [...prev, contact];
    });
    setContactQuery('');
  }, []);

  const handleRemoveContact = React.useCallback((contactId: string) => {
    setSelectedContacts((prev) => prev.filter((contact) => contact.contact_id !== contactId));
  }, []);

  const handleAddEvent = React.useCallback((event: EventResult) => {
    setSelectedEvents((prev) => {
      if (prev.some((item) => item.id === event.id)) return prev;
      return [...prev, event];
    });
    setEventQuery('');
    setEventResults([]);
  }, []);

  const handleRemoveEvent = React.useCallback((eventId: string) => {
    setSelectedEvents((prev) => prev.filter((event) => event.id !== eventId));
  }, []);

  const handleOpenDatePicker = React.useCallback(() => {
    setDraftDate(pickerDate);
    setShowDatePicker(true);
  }, [pickerDate]);

  const handleCloseDatePicker = React.useCallback(() => {
    setShowDatePicker(false);
    setDraftDate(null);
  }, []);

  const handleConfirmDate = React.useCallback(() => {
    if (draftDate) {
      setDueDate(formatIsoDate(draftDate));
    }
    handleCloseDatePicker();
  }, [draftDate, handleCloseDatePicker]);

  const formatEventLabel = React.useCallback((event: EventResult) => {
    const label = event.title?.trim();
    return label && label.length > 0 ? label : event.id;
  }, []);

  const formatEventDate = React.useCallback((event: EventResult) => {
    if (!event.start_date) return null;
    const timestamp = Date.parse(event.start_date);
    if (Number.isNaN(timestamp)) return null;
    return new Date(timestamp).toLocaleDateString();
  }, []);

  const handleSave = React.useCallback(async () => {
    if (!trimmedDescription) {
      showNotice('Add a description for the todo.', 'error');
      return;
    }
    if (trimmedDueDate && !dueDatePattern.test(trimmedDueDate)) {
      showNotice('Use YYYY-MM-DD for the due date.', 'error');
      return;
    }

    setSaving(true);
    try {
      await apiFetch('/mobile/ingest/todo', {
        method: 'POST',
        body: JSON.stringify({
          todo_id: isEditing ? todoIdParam : createTodoId(),
          description: trimmedDescription,
          status: isEditing ? todoStatus : 'pending',
          due_date: trimmedDueDate || null,
          contact_ids: selectedContacts.map((contact) => contact.contact_id),
          event_ids: selectedEvents.map((event) => event.id),
          place_ids: [],
        }),
      });
      showNotice(isEditing ? 'Todo updated.' : 'Todo added.', 'success');
      router.back();
    } catch (error) {
      console.error('[todos] error adding todo', error);
      showNotice(isEditing ? 'Unable to update todo.' : 'Unable to add todo.', 'error');
    } finally {
      setSaving(false);
    }
  }, [
    isEditing,
    router,
    selectedContacts,
    selectedEvents,
    showNotice,
    todoIdParam,
    todoStatus,
    trimmedDescription,
    trimmedDueDate,
  ]);

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      {isEditing && loadingTodo ? (
        <View style={[styles.loadingContainer, { paddingTop: insets.top + 64 }]}>
          <Card style={styles.loadingCard}>
            <ActivityIndicator size="small" color={theme.colors.accentDeep} />
            <Text style={styles.loadingTitle}>Loading todo</Text>
            <Text style={styles.loadingText}>Fetching details and links...</Text>
          </Card>
        </View>
      ) : null}
      <KeyboardAvoidingView
        style={styles.container}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={0}
      >
        <ScrollView
          contentContainerStyle={[
            styles.content,
            {
              paddingTop: insets.top + 64,
              paddingBottom: insets.bottom + 120,
            },
          ]}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.header}>
            <View style={styles.kickerRow}>
              <Ionicons name="sparkles" size={14} color={theme.colors.accentDeep} />
              <Text style={styles.kicker}>{isEditing ? 'Edit todo' : 'New todo'}</Text>
            </View>
            <Text style={styles.title}>{isEditing ? 'Refine this task' : 'Capture what matters'}</Text>
            <Text style={styles.subtitle}>
              {isEditing
                ? 'Update details and save when you are ready.'
                : 'Keep it simple and set a date if it helps.'}
            </Text>
          </View>

          <Card style={styles.formCard}>
            <Text style={styles.label}>Todo</Text>
            {loadingTodo ? <Text style={styles.helper}>Loading todo details...</Text> : null}
            <TextInput
              style={[styles.input, styles.descriptionInput]}
              value={description}
              onChangeText={setDescription}
              placeholder="Write the task in your words"
              placeholderTextColor={theme.colors.mutedInk}
              multiline
            />
            <Text style={styles.helper}>Be specific enough for future you.</Text>

            <View style={styles.sectionDivider} />

            <Text style={styles.label}>Due date</Text>
            <Pressable
              onPress={handleOpenDatePicker}
              style={({ pressed }) => [
                styles.dateField,
                pressed && styles.dateFieldPressed,
              ]}
            >
              <Text style={dueDate ? styles.dateFieldText : styles.dateFieldPlaceholder}>
                {dueDate || 'YYYY-MM-DD (optional)'}
              </Text>
              <Ionicons name="calendar" size={18} color={theme.colors.accentDeep} />
            </Pressable>
            <View style={styles.quickPickRow}>
              {quickDates.map((option) => (
                <Pressable
                  key={option.label}
                  onPress={() => setDueDate(option.value)}
                  style={({ pressed }) => [
                    styles.quickPick,
                    pressed && styles.quickPickPressed,
                  ]}
                >
                  <Text style={styles.quickPickText}>{option.label}</Text>
                </Pressable>
              ))}
              {dueDate ? (
                <Pressable
                  onPress={() => setDueDate('')}
                  style={({ pressed }) => [
                    styles.quickPick,
                    styles.quickPickClear,
                    pressed && styles.quickPickPressed,
                  ]}
                >
                  <Text style={styles.quickPickText}>Clear</Text>
                </Pressable>
              ) : null}
            </View>
            <Text style={styles.helper}>Leave blank to keep it open-ended.</Text>

            <View style={styles.sectionDivider} />

            <Text style={styles.label}>Link contacts</Text>
            <TextInput
              style={styles.input}
              value={contactQuery}
              onChangeText={setContactQuery}
              placeholder="Search contacts"
              placeholderTextColor={theme.colors.mutedInk}
            />
            {filteredContacts.length > 0 ? (
              <View style={styles.suggestionList}>
                {filteredContacts.map((contact) => (
                  <Pressable
                    key={contact.contact_id}
                    onPress={() => handleAddContact(contact)}
                    style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
                  >
                    <Text style={styles.suggestionText}>{contact.display_name}</Text>
                    <Ionicons name="add" size={16} color={theme.colors.accentDeep} />
                  </Pressable>
                ))}
              </View>
            ) : null}
            {selectedContacts.length > 0 ? (
              <View style={styles.chipRow}>
                {selectedContacts.map((contact) => (
                  <Pressable
                    key={contact.contact_id}
                    style={({ pressed }) => [styles.chip, pressed && styles.chipPressed]}
                    onPress={() => handleRemoveContact(contact.contact_id)}
                  >
                    <Text style={styles.chipText}>{contact.display_name}</Text>
                    <Ionicons name="close" size={12} color={theme.colors.mutedInk} />
                  </Pressable>
                ))}
              </View>
            ) : null}
            <Text style={styles.helper}>Add people connected to this task.</Text>

            <View style={styles.sectionDivider} />

            <Text style={styles.label}>Link events</Text>
            <TextInput
              style={styles.input}
              value={eventQuery}
              onChangeText={setEventQuery}
              placeholder="Search events"
              placeholderTextColor={theme.colors.mutedInk}
            />
            {eventsLoading ? <Text style={styles.helper}>Searching events...</Text> : null}
            {!eventsLoading && eventResults.length > 0 ? (
              <View style={styles.suggestionList}>
                {eventResults.map((event) => (
                  <Pressable
                    key={event.id}
                    onPress={() => handleAddEvent(event)}
                    style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
                  >
                    <View style={styles.eventTextWrap}>
                      <Text style={styles.suggestionText}>{formatEventLabel(event)}</Text>
                      {formatEventDate(event) ? (
                        <Text style={styles.eventMeta}>{formatEventDate(event)}</Text>
                      ) : null}
                    </View>
                    <Ionicons name="add" size={16} color={theme.colors.accentDeep} />
                  </Pressable>
                ))}
              </View>
            ) : null}
            {selectedEvents.length > 0 ? (
              <View style={styles.chipRow}>
                {selectedEvents.map((event) => (
                  <Pressable
                    key={event.id}
                    style={({ pressed }) => [styles.chip, pressed && styles.chipPressed]}
                    onPress={() => handleRemoveEvent(event.id)}
                  >
                    <Text style={styles.chipText}>{formatEventLabel(event)}</Text>
                    <Ionicons name="close" size={12} color={theme.colors.mutedInk} />
                  </Pressable>
                ))}
              </View>
            ) : null}
            <Text style={styles.helper}>Type at least 2 characters to search.</Text>
          </Card>

          <Pressable onPress={() => router.back()} style={styles.cancelRow}>
            <Text style={styles.cancelText}>Cancel</Text>
          </Pressable>
        </ScrollView>

        <FloatingSaveButton
          visible
          label={saving ? 'Saving todo' : isEditing ? 'Save changes' : 'Create todo'}
          onPress={handleSave}
          disabled={!canSave}
          loading={saving}
        />
      </KeyboardAvoidingView>
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
              <Text style={styles.datePickerTitle}>Pick a date</Text>
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
    </LinearGradient>
  );
}

export default function NewTodoScreen() {
  return (
    <TopNoticeProvider>
      <NewTodoContent />
    </TopNoticeProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    gap: 16,
  },
  header: {
    gap: 6,
  },
  kickerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  kicker: {
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 2.4,
    color: theme.colors.accentDeep,
    fontWeight: '600',
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
  formCard: {
    padding: 18,
    gap: 10,
  },
  loadingContainer: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'flex-start',
    paddingHorizontal: 20,
    zIndex: 4,
  },
  loadingCard: {
    width: '100%',
    paddingVertical: 16,
    alignItems: 'center',
    gap: 6,
  },
  loadingTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  loadingText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  label: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: theme.colors.ink,
    fontSize: 14,
  },
  dateField: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 12,
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
  descriptionInput: {
    minHeight: 120,
    textAlignVertical: 'top',
  },
  helper: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  quickPickRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 10,
  },
  quickPick: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 14,
    backgroundColor: 'rgba(47, 111, 116, 0.12)',
    borderWidth: 1,
    borderColor: 'rgba(47, 111, 116, 0.2)',
  },
  quickPickClear: {
    backgroundColor: 'rgba(74, 79, 87, 0.08)',
    borderColor: 'rgba(74, 79, 87, 0.2)',
  },
  quickPickPressed: {
    opacity: 0.8,
  },
  quickPickText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  suggestionList: {
    marginTop: 8,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    overflow: 'hidden',
  },
  suggestionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  suggestionPressed: {
    backgroundColor: theme.colors.paleTeal,
  },
  suggestionText: {
    fontSize: 14,
    color: theme.colors.ink,
  },
  eventTextWrap: {
    flex: 1,
    marginRight: 12,
  },
  eventMeta: {
    marginTop: 2,
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 10,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: theme.colors.paleTeal,
  },
  chipPressed: {
    opacity: 0.8,
  },
  chipText: {
    fontSize: 13,
    color: theme.colors.ink,
  },
  sectionDivider: {
    height: 1,
    backgroundColor: theme.colors.line,
    opacity: 0.6,
    marginVertical: 4,
  },
  cancelRow: {
    alignSelf: 'center',
    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  cancelText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.mutedInk,
  },
});
