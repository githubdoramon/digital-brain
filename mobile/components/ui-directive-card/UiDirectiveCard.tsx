import React, { useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import type {
  UiDirectiveBlock,
  UiDirectiveField,
  UiDirectiveOption,
  UiDirectives,
  UiSubmissionInput,
} from '@/chat/uiDirectives';
import { theme } from '@/theme';

import { actionIdForBlock } from './helpers';
import { UiDirectiveChoiceBlock } from './UiDirectiveChoiceBlock';
import { UiDirectiveFormBlock } from './UiDirectiveFormBlock';
import { UiDirectiveInfoBlock } from './UiDirectiveInfoBlock';

type Props = {
  directives: UiDirectives;
  isSubmitting?: boolean;
  onSubmit: (submission: UiSubmissionInput) => void;
};

function fieldStateKey(block: UiDirectiveBlock, field: UiDirectiveField) {
  return `${block.id}:${field.id}`;
}

export function UiDirectiveCard({ directives, isSubmitting = false, onSubmit }: Props) {
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const blocks = useMemo(() => directives.blocks || [], [directives.blocks]);

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
      text_fallback: directives.fallback_text,
    });
  };

  return (
    <View style={styles.container}>
      {blocks.map((block) => (
        <View key={block.id} style={styles.block}>
          {block.title ? <Text style={styles.title}>{block.title}</Text> : null}
          {block.description ? <Text style={styles.description}>{block.description}</Text> : null}

          {block.type === 'clarification_form' ? (
            <UiDirectiveFormBlock
              block={block}
              isSubmitting={isSubmitting}
              getFieldValue={(field) => getFieldValue(block, field)}
              setFieldValue={(field, value) => setFieldValue(block, field, value)}
              onSubmit={() => submitForm(block)}
            />
          ) : null}

          {block.type === 'choice_buttons' ? (
            <UiDirectiveChoiceBlock
              block={block}
              isSubmitting={isSubmitting}
              onSelect={(option) => submitChoice(block, option)}
            />
          ) : null}

          {block.type === 'info_card' ? <UiDirectiveInfoBlock block={block} /> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 12,
  },
  block: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 12,
    gap: 10,
  },
  title: {
    color: theme.colors.ink,
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '700',
  },
  description: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 20,
  },
});

