import React, { useEffect, useMemo, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '@/components/Button';
import { UiDirectiveDateTimePickerSheet } from '@/components/ui-directive-card/UiDirectiveDateTimePickerSheet';
import { theme } from '@/theme';

import { EMPTY_EVENT_DRAFT, EventDraft } from './types';

type Props = {
  visible: boolean;
  initialDraft: EventDraft | null;
  isSubmitting?: boolean;
  onClose: () => void;
  onSave: (draft: EventDraft) => void;
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

export function EventDraftEditorSheet({
  visible,
  initialDraft,
  isSubmitting = false,
  onClose,
  onSave,
}: Props) {
  const resolvedInitial = useMemo(() => initialDraft || EMPTY_EVENT_DRAFT, [initialDraft]);
  const [title, setTitle] = useState(resolvedInitial.title);
  const [summary, setSummary] = useState(resolvedInitial.summary);
  const [when, setWhen] = useState(resolvedInitial.when);
  const [where, setWhere] = useState(resolvedInitial.where);
  const [tagsInput, setTagsInput] = useState(listToInput(resolvedInitial.tags));
  const [typesInput, setTypesInput] = useState(listToInput(resolvedInitial.types));
  const [showDatePicker, setShowDatePicker] = useState(false);

  useEffect(() => {
    if (!visible) return;
    setTitle(resolvedInitial.title);
    setSummary(resolvedInitial.summary);
    setWhen(resolvedInitial.when);
    setWhere(resolvedInitial.where);
    setTagsInput(listToInput(resolvedInitial.tags));
    setTypesInput(listToInput(resolvedInitial.types));
  }, [resolvedInitial, visible]);

  const canSave = !isSubmitting;

  return (
    <>
      <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
        <View style={styles.modalContainer} pointerEvents="box-none">
          <Pressable
            style={styles.modalBackdrop}
            onPress={() => {
              if (!isSubmitting) onClose();
            }}
          />
          <View style={styles.modalSheet} pointerEvents="auto">
            <View style={styles.headerRow}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Close event draft editor"
                disabled={isSubmitting}
                onPress={onClose}
                style={({ pressed }) => [
                  styles.headerAction,
                  pressed && styles.headerActionPressed,
                  isSubmitting && styles.headerActionDisabled,
                ]}
              >
                <Text style={styles.cancelText}>Close</Text>
              </Pressable>
              <Text style={styles.title}>Edit event draft</Text>
              <View style={styles.headerActionSpacer} />
            </View>

            <ScrollView
              showsVerticalScrollIndicator={false}
              contentContainerStyle={styles.content}
              keyboardShouldPersistTaps="handled"
            >
              <View style={styles.fieldWrap}>
                <Text style={styles.label}>Title</Text>
                <TextInput
                  value={title}
                  editable={!isSubmitting}
                  onChangeText={setTitle}
                  placeholder="Add a short title"
                  placeholderTextColor={theme.colors.mutedInk}
                  style={styles.input}
                />
              </View>

              <View style={styles.fieldWrap}>
                <Text style={styles.label}>Summary</Text>
                <TextInput
                  value={summary}
                  editable={!isSubmitting}
                  onChangeText={setSummary}
                  placeholder="Capture what happened"
                  placeholderTextColor={theme.colors.mutedInk}
                  multiline
                  style={[styles.input, styles.textarea]}
                />
              </View>

              <View style={styles.fieldWrap}>
                <Text style={styles.label}>When</Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Select event time"
                  disabled={isSubmitting}
                  onPress={() => setShowDatePicker(true)}
                  style={({ pressed }) => [
                    styles.dateField,
                    pressed && styles.dateFieldPressed,
                    isSubmitting && styles.disabledField,
                  ]}
                >
                  <Text style={when ? styles.dateValue : styles.datePlaceholder}>
                    {when ? formatWhen(when) : 'Pick date and time'}
                  </Text>
                </Pressable>
                {when ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Clear event time"
                    disabled={isSubmitting}
                    onPress={() => setWhen('')}
                    style={({ pressed }) => [
                      styles.clearLink,
                      pressed && styles.headerActionPressed,
                      isSubmitting && styles.headerActionDisabled,
                    ]}
                  >
                    <Text style={styles.clearLinkText}>Clear time</Text>
                  </Pressable>
                ) : null}
              </View>

              <View style={styles.fieldWrap}>
                <Text style={styles.label}>Where</Text>
                <TextInput
                  value={where}
                  editable={!isSubmitting}
                  onChangeText={setWhere}
                  placeholder="Location"
                  placeholderTextColor={theme.colors.mutedInk}
                  style={styles.input}
                />
              </View>

              <View style={styles.fieldWrap}>
                <Text style={styles.label}>Tags</Text>
                <TextInput
                  value={tagsInput}
                  editable={!isSubmitting}
                  onChangeText={setTagsInput}
                  placeholder="work, meeting, personal"
                  placeholderTextColor={theme.colors.mutedInk}
                  style={styles.input}
                />
              </View>

              <View style={styles.fieldWrap}>
                <Text style={styles.label}>Types</Text>
                <TextInput
                  value={typesInput}
                  editable={!isSubmitting}
                  onChangeText={setTypesInput}
                  placeholder="meeting, travel, personal"
                  placeholderTextColor={theme.colors.mutedInk}
                  style={styles.input}
                />
              </View>
            </ScrollView>

            <View style={styles.footer}>
              <Button
                label="Save changes"
                variant="primary"
                disabled={!canSave}
                onPress={() => {
                  onSave({
                    title: title.trim(),
                    summary: summary.trim(),
                    when: when.trim(),
                    where: where.trim(),
                    tags: inputToList(tagsInput),
                    types: inputToList(typesInput),
                  });
                }}
                style={styles.saveButton}
              />
            </View>
          </View>
        </View>
      </Modal>

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
    </>
  );
}

const styles = StyleSheet.create({
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
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    maxHeight: '90%',
    zIndex: 2,
  },
  headerRow: {
    minHeight: 52,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  headerAction: {
    minWidth: 56,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: theme.radius.md,
  },
  headerActionSpacer: {
    minWidth: 56,
  },
  headerActionPressed: {
    opacity: 0.75,
  },
  headerActionDisabled: {
    opacity: 0.5,
  },
  cancelText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.mutedInk,
  },
  title: {
    fontSize: 15,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  content: {
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 16,
    gap: 12,
  },
  fieldWrap: {
    gap: 8,
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
  disabledField: {
    opacity: 0.5,
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
  clearLinkText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.accentDeep,
  },
  footer: {
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 16,
  },
  saveButton: {
    minHeight: 46,
  },
});
