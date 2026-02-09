import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { UiDirectiveBlock, UiDirectiveOption } from '@/chat/uiDirectives';
import { theme } from '@/theme';

type Props = {
  block: UiDirectiveBlock;
  isSubmitting: boolean;
  onSelect: (option: UiDirectiveOption) => void;
};

export function UiDirectiveChoiceBlock({ block, isSubmitting, onSelect }: Props) {
  const options = block.options || [];

  return (
    <View style={styles.optionRow}>
      {options.map((option) => (
        <Pressable
          key={`${block.id}:${option.id}`}
          accessibilityRole="button"
          accessibilityLabel={option.label}
          disabled={isSubmitting}
          onPress={() => onSelect(option)}
          style={({ pressed }) => [
            styles.choiceButton,
            pressed && styles.choiceButtonPressed,
            isSubmitting && styles.buttonDisabled,
          ]}
        >
          <Text style={styles.choiceButtonText}>{option.label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  optionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  choiceButton: {
    minHeight: 44,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  choiceButtonPressed: {
    backgroundColor: theme.colors.paleTeal,
    borderColor: theme.colors.teal,
  },
  choiceButtonText: {
    color: theme.colors.ink,
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '600',
  },
  buttonDisabled: {
    opacity: 0.6,
  },
});

