import React, { useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import type { EventResolvedStatus } from '@/chat/threads';
import type {
  UiDirectiveBlock,
  UiDirectiveField,
  UiDirectiveOption,
  UiDirectives,
  UiSubmissionInput,
} from '@/chat/uiDirectives';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

import { actionIdForBlock } from './helpers';
import { UiDirectiveChoiceBlock } from './UiDirectiveChoiceBlock';
import { UiDirectiveFormBlock } from './UiDirectiveFormBlock';
import { UiDirectiveInfoBlock } from './UiDirectiveInfoBlock';

type Props = {
  directives: UiDirectives;
  isSubmitting?: boolean;
  resolvedStatus?: EventResolvedStatus;
  onFieldFocus?: () => void;
  onSubmit: (submission: UiSubmissionInput) => void;
};

function fieldStateKey(block: UiDirectiveBlock, field: UiDirectiveField) {
  return `${block.id}:${field.id}`;
}

function formatFieldLabel(fieldId: string) {
  return fieldId
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\w/, (char) => char.toUpperCase());
}

function optionLabelForField(field: UiDirectiveField | undefined, value: string): string {
  if (!field || !value) return value;
  const match = (field.options || []).find((option) => option.id === value);
  return match?.label || value;
}

function fallbackTextForForm(block: UiDirectiveBlock, values: Record<string, unknown>, defaultText: string) {
  const entries = Object.entries(values)
    .map(([fieldId, rawValue]) => {
      const value = String(rawValue ?? '').trim();
      if (!value) return null;
      const field = (block.fields || []).find((candidate) => candidate.id === fieldId);
      const normalizedValue = optionLabelForField(field, value);
      const label = field?.label || formatFieldLabel(fieldId);
      return {
        fieldId,
        value: normalizedValue,
        label,
      };
    })
    .filter((entry): entry is { fieldId: string; value: string; label: string } => Boolean(entry));

  if (entries.length === 0) return defaultText;
  if (entries.length === 1) return entries[0].value;
  return entries.map((entry) => `${entry.label}: ${entry.value}`).join('; ');
}

function toneForBlock(block: UiDirectiveBlock) {
  if (block.type === 'choice_buttons') {
    return {
      label: 'Choice',
      backgroundColor: '#fde9e6',
      textColor: theme.colors.accentDeep,
    };
  }
  if (block.type === 'info_card') {
    return {
      label: 'Info',
      backgroundColor: theme.colors.paleTeal,
      textColor: theme.colors.teal,
    };
  }
  return {
    label: 'Follow-up',
    backgroundColor: '#f7f2ec',
    textColor: theme.colors.ink,
  };
}

const EVENT_PREVIEW_BLOCK_PREFIX = 'event_preview:';
const EVENT_CONFIRM_ACTION_ID = 'event_confirmation_action';
const EVENT_EDIT_OPTION_PREFIX = 'edit:';

type EventEditAction = {
  blockId: string;
  actionId: string;
  option: UiDirectiveOption;
};

export function UiDirectiveCard({
  directives,
  isSubmitting = false,
  resolvedStatus,
  onFieldFocus,
  onSubmit,
}: Props) {
  const isResolved = Boolean(resolvedStatus);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const blocks = useMemo(() => directives.blocks || [], [directives.blocks]);
  const eventEditActionsByPreviewId = useMemo(() => {
    const mapping = new Map<string, EventEditAction>();
    for (const block of blocks) {
      if (block.type !== 'choice_buttons') continue;
      if (block.action_id !== EVENT_CONFIRM_ACTION_ID) continue;
      const actionId = actionIdForBlock(block);
      for (const option of block.options || []) {
        if (!option.id.startsWith(EVENT_EDIT_OPTION_PREFIX)) continue;
        const previewId = option.id.slice(EVENT_EDIT_OPTION_PREFIX.length).trim();
        if (!previewId) continue;
        mapping.set(previewId, {
          blockId: block.id,
          actionId,
          option,
        });
      }
    }
    return mapping;
  }, [blocks]);

  const setFieldValue = (block: UiDirectiveBlock, field: UiDirectiveField, value: string) => {
    setFormValues((prev) => ({
      ...prev,
      [fieldStateKey(block, field)]: value,
    }));
  };

  const getFieldValue = (block: UiDirectiveBlock, field: UiDirectiveField) =>
    formValues[fieldStateKey(block, field)] ?? '';

  const submitChoice = (block: UiDirectiveBlock, option: UiDirectiveOption) => {
    onSubmit({
      block_id: block.id,
      action_id: actionIdForBlock(block),
      values: { option_id: option.id, option_label: option.label },
      text_fallback: option.label,
    });
  };

  const submitForm = (block: UiDirectiveBlock) => {
    const fields = block.fields || [];
    const values: Record<string, unknown> = {};

    for (const field of fields) {
      const value = getFieldValue(block, field).trim();
      if (field.required && !value) {
        return;
      }
      if (value) {
        values[field.id] = value;
      }
    }

    onSubmit({
      block_id: block.id,
      action_id: actionIdForBlock(block),
      values,
      text_fallback: fallbackTextForForm(block, values, directives.fallback_text),
    });
  };

  return (
    <View style={styles.container}>
      {isResolved && (
        <View
          style={[
            styles.resolvedBanner,
            resolvedStatus === 'created'
              ? styles.resolvedBannerCreated
              : styles.resolvedBannerCancelled,
          ]}
        >
          <Ionicons
            name={resolvedStatus === 'created' ? 'checkmark-circle' : 'close-circle'}
            size={16}
            color={resolvedStatus === 'created' ? theme.colors.teal : theme.colors.mutedInk}
          />
          <Text
            style={[
              styles.resolvedBannerText,
              resolvedStatus === 'created'
                ? styles.resolvedTextCreated
                : styles.resolvedTextCancelled,
            ]}
          >
            {resolvedStatus === 'created' ? 'Event created' : 'Event cancelled'}
          </Text>
        </View>
      )}
      {blocks.map((block) => {
        const tone = toneForBlock(block);
        const previewId =
          block.type === 'info_card' && block.id.startsWith(EVENT_PREVIEW_BLOCK_PREFIX)
            ? block.id.slice(EVENT_PREVIEW_BLOCK_PREFIX.length).trim()
            : '';
        const editAction =
          previewId && block.type === 'info_card'
            ? eventEditActionsByPreviewId.get(previewId)
            : undefined;

        return (
          <Card key={block.id} variant="elevated" style={styles.blockCard}>
            <View style={styles.headerRow}>
              <View
                style={[
                  styles.badge,
                  { backgroundColor: tone.backgroundColor },
                ]}
              >
                <Text style={[styles.badgeText, { color: tone.textColor }]}>
                  {tone.label}
                </Text>
              </View>
              {editAction && !isResolved ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Edit event draft"
                  disabled={isSubmitting}
                  onPress={() =>
                    onSubmit({
                      block_id: editAction.blockId,
                      action_id: editAction.actionId,
                      values: {
                        option_id: editAction.option.id,
                        option_label: editAction.option.label,
                      },
                      text_fallback: editAction.option.label,
                    })
                  }
                  style={({ pressed }) => [
                    styles.editButton,
                    pressed && styles.editButtonPressed,
                    isSubmitting && styles.editButtonDisabled,
                  ]}
                >
                  <Ionicons name="create" size={24} color={theme.colors.accentDeep} />
                </Pressable>
              ) : null}
            </View>

            {block.title ? <Text style={styles.title}>{block.title}</Text> : null}
            {block.description ? <Text style={styles.description}>{block.description}</Text> : null}

            {block.type === 'clarification_form' && !isResolved ? (
              <UiDirectiveFormBlock
                block={block}
                isSubmitting={isSubmitting}
                getFieldValue={(field) => getFieldValue(block, field)}
                setFieldValue={(field, value) => setFieldValue(block, field, value)}
                onFieldFocus={onFieldFocus}
                onSubmit={() => submitForm(block)}
              />
            ) : null}

            {block.type === 'choice_buttons' ? (
              <UiDirectiveChoiceBlock
                block={block}
                isSubmitting={isSubmitting}
                resolvedStatus={resolvedStatus}
                onSelect={(option) => submitChoice(block, option)}
              />
            ) : null}

            {block.type === 'info_card' ? <UiDirectiveInfoBlock block={block} /> : null}
          </Card>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 14,
  },
  blockCard: {
    paddingHorizontal: 14,
    paddingVertical: 14,
    gap: 10,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  badge: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.colors.line,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  badgeText: {
    fontSize: 11,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
    fontWeight: '700',
  },
  editButton: {
    minWidth: 44,
    minHeight: 44,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.colors.line,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  editButtonPressed: {
    opacity: 0.75,
  },
  editButtonDisabled: {
    opacity: 0.5,
  },
  title: {
    color: theme.colors.ink,
    fontSize: 16,
    lineHeight: 22,
    fontWeight: '700',
  },
  description: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 21,
  },
  resolvedBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: theme.radius.md,
  },
  resolvedBannerCreated: {
    backgroundColor: theme.colors.paleTeal,
  },
  resolvedBannerCancelled: {
    backgroundColor: '#f2f0ed',
  },
  resolvedBannerText: {
    fontSize: 13,
    fontWeight: '600',
    lineHeight: 18,
  },
  resolvedTextCreated: {
    color: theme.colors.teal,
  },
  resolvedTextCancelled: {
    color: theme.colors.mutedInk,
  },
});
