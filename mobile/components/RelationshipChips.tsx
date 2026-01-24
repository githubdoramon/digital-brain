import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '@/theme';

type RelationshipChip = {
  label: string;
};

type RelationshipChipsProps = {
  chips: RelationshipChip[];
};

export function RelationshipChips({ chips }: RelationshipChipsProps) {
  if (!chips.length) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyText}>No relationships yet</Text>
      </View>
    );
  }

  return (
    <View style={styles.wrap}>
      {chips.map((chip) => (
        <View key={chip.label} style={styles.chip}>
          <Text style={styles.chipText}>{chip.label}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: theme.colors.paleTeal,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  chipText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  empty: {
    paddingVertical: 8,
  },
  emptyText: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
});
