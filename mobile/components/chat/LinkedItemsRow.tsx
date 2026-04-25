import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { isLinkedItemNavigable, type LinkedItem } from '@/chat/linkedItems';
import { theme } from '@/theme';

type LinkedItemsRowProps = {
  items: LinkedItem[];
  onPressItem: (item: LinkedItem) => void;
  disabled?: boolean;
};

function iconForItem(item: LinkedItem): keyof typeof Ionicons.glyphMap {
  if (item.entity_type === 'event') return 'calendar-outline';
  if (item.entity_type === 'document') return 'document-text-outline';
  if (item.entity_type === 'contact') return 'person-outline';
  if (item.entity_type === 'place') return 'location-outline';
  throw new Error(`Unsupported linked item type: ${item.entity_type}`);
}

export function LinkedItemsRow({ items, onPressItem, disabled = false }: LinkedItemsRowProps) {
  const navigableItems = items.filter(isLinkedItemNavigable);
  if (!navigableItems.length) return null;

  return (
    <View style={styles.wrapper}>
      <Text style={styles.label}>Related items</Text>
      <View style={styles.row}>
        {navigableItems.map((item) => (
          <Pressable
            key={`${item.entity_type}:${item.entity_id}`}
            onPress={() => onPressItem(item)}
            disabled={disabled}
            style={({ pressed }) => [
              styles.pill,
              pressed && !disabled && styles.pillPressed,
              disabled && styles.pillDisabled,
            ]}
          >
            <Ionicons name={iconForItem(item)} size={14} color={theme.colors.teal} />
            <Text numberOfLines={1} style={styles.title}>
              {item.title}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginTop: 8,
    gap: 6,
  },
  label: {
    fontSize: 12,
    color: theme.colors.mutedInk,
    fontWeight: '600',
  },
  row: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  pill: {
    borderWidth: 1,
    borderColor: '#bfdad7',
    backgroundColor: theme.colors.paleTeal,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 6,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    maxWidth: '100%',
  },
  pillPressed: {
    opacity: 0.7,
  },
  pillDisabled: {
    opacity: 0.6,
  },
  title: {
    color: theme.colors.teal,
    fontSize: 13,
    fontWeight: '600',
    maxWidth: 220,
  },
});
