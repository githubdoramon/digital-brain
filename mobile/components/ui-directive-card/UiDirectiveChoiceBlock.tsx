import React from 'react';
import { StyleSheet, View } from 'react-native';

import type { CommandResolvedStatus } from '@/chat/threads';
import type { UiDirectiveBlock, UiDirectiveOption } from '@/chat/uiDirectives';
import { Button } from '@/components/Button';

type Props = {
  block: UiDirectiveBlock;
  isSubmitting: boolean;
  submittingOptionId?: string | null;
  resolvedStatus?: CommandResolvedStatus;
  onSelect: (option: UiDirectiveOption) => void;
};

const EVENT_CONFIRM_ACTION_ID = 'event_confirmation_action';
const CONTACT_CONFIRM_ACTION_ID = 'contact_confirmation_action';
const HIDDEN_EVENT_OPTION_PREFIXES = ['edit:'];

export function UiDirectiveChoiceBlock({ block, isSubmitting, submittingOptionId, resolvedStatus, onSelect }: Props) {
  const isResolved = Boolean(resolvedStatus);

  const options = (block.options || []).filter((option) => {
    if (
      block.action_id !== EVENT_CONFIRM_ACTION_ID &&
      block.action_id !== CONTACT_CONFIRM_ACTION_ID
    ) {
      return true;
    }
    return !HIDDEN_EVENT_OPTION_PREFIXES.some((prefix) => option.id.startsWith(prefix));
  });

  return (
    <View style={[styles.optionColumn, isResolved && styles.resolvedColumn]}>
      {options.map((option) => (
        <Button
          key={`${block.id}:${option.id}`}
          label={option.label}
          variant={isResolved ? 'secondary' : buttonVariant(option)}
          disabled={isSubmitting || isResolved}
          loading={Boolean(isSubmitting && submittingOptionId === option.id)}
          loadingLabel={buttonLoadingLabel(option.label)}
          onPress={() => onSelect(option)}
          style={styles.choiceButton}
        />
      ))}
    </View>
  );
}

function buttonLoadingLabel(label: string): string {
  const trimmed = label.trim();
  if (!trimmed) return 'Working';
  if (/cancel/i.test(trimmed)) return 'Cancelling';
  if (/edit/i.test(trimmed)) return 'Opening editor';
  if (/create/i.test(trimmed)) return 'Creating';
  if (/update/i.test(trimmed)) return 'Updating';
  if (/save/i.test(trimmed)) return 'Saving';
  if (/confirm/i.test(trimmed)) return 'Confirming';
  if (/continue|proceed/i.test(trimmed)) return 'Continuing';
  if (/yes|ok/i.test(trimmed)) return 'Working';
  return trimmed;
}

function buttonVariant(option: UiDirectiveOption): 'primary' | 'secondary' {
  const signal = `${option.id} ${option.label}`.toLowerCase();
  const isPrimary = [
    'confirm',
    'yes',
    'create',
    'update',
    'save',
    'submit',
    'continue',
    'proceed',
    'ok',
  ].some((keyword) => signal.includes(keyword));
  return isPrimary ? 'primary' : 'secondary';
}

const styles = StyleSheet.create({
  optionColumn: {
    gap: 10,
  },
  resolvedColumn: {
    opacity: 0.5,
  },
  choiceButton: {
    minHeight: 44,
  },
});
