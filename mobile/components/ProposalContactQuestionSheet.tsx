import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { Button } from '@/components/Button';
import { theme } from '@/theme';
import { contactChoiceOptions, type ContactChoice } from '@/utils/proposalActions';

type Props = {
  choice?: ContactChoice;
  selectedValue?: string;
  onSelect: (value: string) => void;
  onClose: () => void;
};

export function ProposalContactQuestionSheet({ choice, selectedValue, onSelect, onClose }: Props) {
  const options = choice ? contactChoiceOptions(choice) : [];
  return (
    <BottomSheet visible={!!choice} onClose={onClose}>
      <Text accessibilityRole="header" style={styles.title}>
        Who is {choice?.display_name}?
      </Text>
      <Text style={styles.subtitle}>Choose who this suggestion is about.</Text>
      <ScrollView style={styles.scroll} contentContainerStyle={styles.options}>
        {options.map((option) => {
          const selected = option.value === selectedValue;
          return (
            <Pressable
              key={option.value}
              accessibilityRole="radio"
              accessibilityState={{ checked: selected }}
              onPress={() => onSelect(option.value)}
              style={[styles.option, selected && styles.selected]}
            >
              <Ionicons
                name={option.value === 'create' ? 'person-add-outline' : 'person-outline'}
                size={20}
                color={theme.colors.teal}
              />
              <Text style={styles.label}>{option.label}</Text>
              {selected ? <Ionicons name="checkmark" size={20} color={theme.colors.teal} /> : null}
            </Pressable>
          );
        })}
        {!options.length ? (
          <Text style={styles.subtitle}>
            No contacts are available to choose. You can leave this suggestion for later or dismiss
            it.
          </Text>
        ) : null}
      </ScrollView>
      <Button label="Back" variant="secondary" onPress={onClose} />
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 20, fontWeight: '700', color: theme.colors.ink },
  subtitle: { fontSize: 14, lineHeight: 20, color: theme.colors.mutedInk, marginTop: 8 },
  scroll: { maxHeight: 360, marginVertical: 16 },
  options: { gap: 10 },
  option: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    minHeight: 56,
    padding: 14,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
  },
  selected: { borderColor: theme.colors.teal, backgroundColor: theme.colors.paleTeal },
  label: { flex: 1, fontSize: 15, color: theme.colors.ink },
});
