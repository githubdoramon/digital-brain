import React from 'react';
import { StyleSheet, View } from 'react-native';

import type { UiDirectiveBlock, UiDirectiveOption } from '@/chat/uiDirectives';
import { Button } from '@/components/Button';

type Props = {
  block: UiDirectiveBlock;
  isSubmitting: boolean;
  onSelect: (option: UiDirectiveOption) => void;
};

export function UiDirectiveChoiceBlock({ block, isSubmitting, onSelect }: Props) {
  const options = block.options || [];

  return (
    <View style={styles.optionColumn}>
      {options.map((option) => (
        <Button
          key={`${block.id}:${option.id}`}
          label={option.label}
          variant={buttonVariant(option)}
          disabled={isSubmitting}
          onPress={() => onSelect(option)}
          style={styles.choiceButton}
        />
      ))}
    </View>
  );
}

function buttonVariant(option: UiDirectiveOption): 'primary' | 'secondary' {
  const signal = `${option.id} ${option.label}`.toLowerCase();
  const isPrimary = [
    'confirm',
    'yes',
    'create',
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
  choiceButton: {
    minHeight: 44,
  },
});
