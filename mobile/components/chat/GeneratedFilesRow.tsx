import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { generatedFileLabel, type GeneratedFile } from '@/chat/generatedFiles';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type GeneratedFilesRowProps = {
  files: GeneratedFile[];
  onPressFile: (file: GeneratedFile) => void;
  disabled?: boolean;
};

export function GeneratedFilesRow({
  files,
  onPressFile,
  disabled = false,
}: GeneratedFilesRowProps) {
  if (!files.length) return null;

  return (
    <View style={styles.wrapper}>
      <Text style={styles.label}>Downloads</Text>
      <View style={styles.row}>
        {files.map((file) => (
          <Pressable
            key={`${file.kind}:${file.artifact_id}`}
            onPress={() => onPressFile(file)}
            disabled={disabled}
            style={({ pressed }) => [
              styles.pill,
              pressed && !disabled && styles.pillPressed,
              disabled && styles.pillDisabled,
            ]}
          >
            <Ionicons name="download-outline" size={14} color={theme.colors.teal} />
            <Text numberOfLines={1} style={styles.title}>
              {generatedFileLabel(file)}
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
