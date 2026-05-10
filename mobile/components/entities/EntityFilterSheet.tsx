import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { ScrollView, StyleSheet, Switch, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { Button } from '@/components/Button';
import { theme } from '@/theme';

import { ENTITY_META, EMPTY_ENTITY_FILTERS, type EntityFilters, type EntityKind } from './types';

type EntityFilterSheetProps = {
  visible: boolean;
  filters: EntityFilters;
  chips: { id: string; kind: EntityKind; label: string }[];
  onApply: (filters: EntityFilters) => void;
  onRemove: (kind: EntityKind, id: string) => void;
  onClose: () => void;
};

export function EntityFilterSheet({ visible, filters, chips, onApply, onRemove, onClose }: EntityFilterSheetProps) {

  return (
    <BottomSheet visible={visible} onClose={onClose}>
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.title}>Filters</Text>
          <Text style={styles.subtitle}>Mix contacts, places, and events to narrow the list.</Text>
        </View>
        <Pressable onPress={onClose} style={styles.closeButton} accessibilityRole="button">
          <Ionicons name="close" size={20} color={theme.colors.ink} />
        </Pressable>
      </View>

      <ScrollView style={styles.optionsScroll} contentContainerStyle={styles.optionsContent}>
        {(['contacts', 'places', 'events', 'documents'] as const).map((kind) => {
          const sectionChips = chips.filter((chip) => chip.kind === kind);
          return (
            <View key={kind} style={styles.section}>
              <View style={styles.sectionHeader}>
                <Ionicons name={ENTITY_META[kind].icon} size={16} color={theme.colors.teal} />
                <Text style={styles.sectionTitle}>{ENTITY_META[kind].label}</Text>
                {sectionChips.length ? <Text style={styles.sectionCount}>{sectionChips.length}</Text> : null}
              </View>
              {sectionChips.length ? (
                <View style={styles.chipWrap}>
                  {sectionChips.map((chip) => (
                    <Pressable
                      key={`${chip.kind}:${chip.id}`}
                      onPress={() => onRemove(chip.kind, chip.id)}
                      style={({ pressed }) => [styles.activeFilterChip, pressed && styles.activeFilterChipPressed]}
                    >
                      <Text style={styles.activeFilterChipText}>{chip.label}</Text>
                      <Ionicons name="close" size={14} color={theme.colors.teal} />
                    </Pressable>
                  ))}
                </View>
              ) : (
                <Text style={styles.emptyText}>No selected {ENTITY_META[kind].label.toLowerCase()} yet.</Text>
              )}
              {kind === 'events' ? (
                <View style={styles.toggleRow}>
                  <View style={styles.toggleCopy}>
                    <Text style={styles.toggleTitle}>Include future events</Text>
                    <Text style={styles.toggleDescription}>Show upcoming events in the main events list and event filters.</Text>
                  </View>
                  <Switch
                    value={filters.includeFutureEvents}
                    onValueChange={(value) => onApply({ ...filters, includeFutureEvents: value })}
                    trackColor={{ false: theme.colors.line, true: theme.colors.paleTeal }}
                    thumbColor={filters.includeFutureEvents ? theme.colors.accent : '#f2f2f2'}
                  />
                </View>
              ) : null}
            </View>
          );
        })}
      </ScrollView>

      <View style={styles.footer}>
        <Button
          label="Clear all"
          variant="secondary"
          onPress={() => {
            onApply({
              contactIds: [...EMPTY_ENTITY_FILTERS.contactIds],
              placeIds: [...EMPTY_ENTITY_FILTERS.placeIds],
              eventIds: [...EMPTY_ENTITY_FILTERS.eventIds],
              includeFutureEvents: EMPTY_ENTITY_FILTERS.includeFutureEvents,
            });
            onClose();
          }}
          style={styles.footerButton}
        />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
  },
  title: {
    fontSize: 20,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    marginTop: 4,
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  closeButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f6efe8',
  },
  optionsScroll: {
    maxHeight: 460,
    marginTop: 14,
  },
  optionsContent: {
    gap: 18,
    paddingBottom: 8,
  },
  section: {
    gap: 10,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  sectionCount: {
    fontSize: 12,
    fontWeight: '700',
    color: theme.colors.teal,
    backgroundColor: theme.colors.paleTeal,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  chipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  activeFilterChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    backgroundColor: theme.colors.paleTeal,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  activeFilterChipPressed: {
    opacity: 0.86,
  },
  activeFilterChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  emptyText: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  toggleRow: {
    marginTop: 2,
    paddingHorizontal: 2,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  toggleCopy: {
    flex: 1,
    gap: 3,
  },
  toggleTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  toggleDescription: {
    fontSize: 12,
    lineHeight: 17,
    color: theme.colors.mutedInk,
  },
  footer: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 16,
    marginBottom: 4,
  },
  footerButton: {
    flex: 1,
  },
});
