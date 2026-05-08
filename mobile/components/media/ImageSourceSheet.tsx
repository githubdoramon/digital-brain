import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { theme } from '@/theme';

export type ImagePickSource = 'camera' | 'library';

type Props = {
  visible: boolean;
  onSelect: (source: ImagePickSource) => void;
  onClose: () => void;
};

const OPTIONS: {
  key: ImagePickSource;
  label: string;
  description: string;
  icon: React.ComponentProps<typeof Ionicons>['name'];
}[] = [
  {
    key: 'camera',
    label: 'Take photo',
    description: 'Capture a new picture now',
    icon: 'camera-outline',
  },
  {
    key: 'library',
    label: 'Choose from gallery',
    description: 'Pick an image from your library',
    icon: 'images-outline',
  },
];

export function ImageSourceSheet({ visible, onSelect, onClose }: Props) {
  return (
    <BottomSheet visible={visible} onClose={onClose} baseBottomPadding={20}>
      <Text style={styles.title}>Add photo</Text>
      <Text style={styles.subtitle}>Choose where to get the picture from.</Text>

      <View style={styles.optionList}>
        {OPTIONS.map((option) => (
          <Pressable
            key={option.key}
            onPress={() => onSelect(option.key)}
            style={({ pressed }) => [styles.optionRow, pressed && styles.optionRowPressed]}
          >
            <View style={styles.iconWrap}>
              <Ionicons name={option.icon} size={20} color={theme.colors.accentDeep} />
            </View>
            <View style={styles.copyWrap}>
              <Text style={styles.optionLabel}>{option.label}</Text>
              <Text style={styles.optionDescription}>{option.description}</Text>
            </View>
          </Pressable>
        ))}
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  title: {
    fontSize: 22,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    marginTop: 6,
    fontSize: 14,
    lineHeight: 20,
    color: theme.colors.mutedInk,
  },
  optionList: {
    marginTop: 18,
    gap: 10,
  },
  optionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#eadfd3',
    backgroundColor: '#fffaf6',
    paddingHorizontal: 16,
    paddingVertical: 15,
  },
  optionRowPressed: {
    opacity: 0.86,
  },
  iconWrap: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f7ede3',
  },
  copyWrap: {
    flex: 1,
    gap: 2,
  },
  optionLabel: {
    fontSize: 16,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  optionDescription: {
    fontSize: 13,
    lineHeight: 18,
    color: theme.colors.mutedInk,
  },
});
