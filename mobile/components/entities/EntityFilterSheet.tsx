import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { Button } from '@/components/Button';
import { theme } from '@/theme';

import { filterOptionsBySearch } from './helpers';
import { ENTITY_META, EMPTY_ENTITY_FILTERS, type EntityFilterOption, type EntityFilters, type EntityKind } from './types';

type EntityFilterSheetProps = {
  visible: boolean;
  filters: EntityFilters;
  options: EntityFilterOption[];
  onApply: (filters: EntityFilters) => void;
  onClose: () => void;
};

function cloneFilters(filters: EntityFilters): EntityFilters {
  return {
    contactIds: [...filters.contactIds],
    placeIds: [...filters.placeIds],
    eventIds: [...filters.eventIds],
  };
}

export function EntityFilterSheet({ visible, filters, options, onApply, onClose }: EntityFilterSheetProps) {
  const [draftFilters, setDraftFilters] = React.useState<EntityFilters>(() => cloneFilters(filters));
  const [query, setQuery] = React.useState('');

  React.useEffect(() => {
    if (!visible) return;
    setDraftFilters(cloneFilters(filters));
    setQuery('');
  }, [filters, visible]);

  const toggleOption = React.useCallback((kind: EntityKind, id: string) => {
    setDraftFilters((current) => {
      const key = kind === 'contacts' ? 'contactIds' : kind === 'places' ? 'placeIds' : 'eventIds';
      const nextValues = current[key].includes(id)
        ? current[key].filter((value) => value !== id)
        : current[key].concat(id);
      return {
        ...current,
        [key]: nextValues,
      };
    });
  }, []);

  const filteredOptions = React.useMemo(() => filterOptionsBySearch(options, query), [options, query]);

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

      <TextInput
        value={query}
        onChangeText={setQuery}
        placeholder="Search filter options"
        placeholderTextColor={theme.colors.mutedInk}
        style={styles.searchInput}
      />

      <ScrollView style={styles.optionsScroll} contentContainerStyle={styles.optionsContent}>
        {(['contacts', 'places', 'events'] as const).map((kind) => {
          const sectionOptions = filteredOptions.filter((option) => option.kind === kind);
          const selectedIds =
            kind === 'contacts'
              ? draftFilters.contactIds
              : kind === 'places'
                ? draftFilters.placeIds
                : draftFilters.eventIds;
          return (
            <View key={kind} style={styles.section}>
              <View style={styles.sectionHeader}>
                <Ionicons name={ENTITY_META[kind].icon} size={16} color={theme.colors.teal} />
                <Text style={styles.sectionTitle}>{ENTITY_META[kind].label}</Text>
                {selectedIds.length ? <Text style={styles.sectionCount}>{selectedIds.length}</Text> : null}
              </View>
              {sectionOptions.length ? (
                sectionOptions.map((option) => {
                  const selected = selectedIds.includes(option.id);
                  return (
                    <Pressable
                      key={`${option.kind}:${option.id}`}
                      onPress={() => toggleOption(option.kind, option.id)}
                      style={({ pressed }) => [
                        styles.optionRow,
                        selected && styles.optionRowSelected,
                        pressed && styles.optionRowPressed,
                      ]}
                    >
                      <View style={styles.optionTextWrap}>
                        <Text style={styles.optionTitle}>{option.label}</Text>
                        {option.description ? (
                          <Text style={styles.optionDescription} numberOfLines={2}>
                            {option.description}
                          </Text>
                        ) : null}
                      </View>
                      <View style={[styles.checkWrap, selected && styles.checkWrapSelected]}>
                        {selected ? <Ionicons name="checkmark" size={16} color="#fff" /> : null}
                      </View>
                    </Pressable>
                  );
                })
              ) : (
                <Text style={styles.emptyText}>No {ENTITY_META[kind].label.toLowerCase()} match this search.</Text>
              )}
            </View>
          );
        })}
      </ScrollView>

      <View style={styles.footer}>
        <Button
          label="Clear all"
          variant="secondary"
          onPress={() => setDraftFilters(cloneFilters(EMPTY_ENTITY_FILTERS))}
          style={styles.footerButton}
        />
        <Button
          label="Apply filters"
          onPress={() => {
            onApply(draftFilters);
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
  searchInput: {
    marginTop: 14,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: theme.colors.ink,
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
  optionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  optionRowSelected: {
    borderColor: theme.colors.teal,
    backgroundColor: '#f4faf9',
  },
  optionRowPressed: {
    opacity: 0.86,
  },
  optionTextWrap: {
    flex: 1,
    gap: 4,
  },
  optionTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  optionDescription: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  checkWrap: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkWrapSelected: {
    borderColor: theme.colors.teal,
    backgroundColor: theme.colors.teal,
  },
  emptyText: {
    fontSize: 13,
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
