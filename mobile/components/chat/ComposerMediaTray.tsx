import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { Image, ScrollView, StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import type { ComposerMediaAttachment } from '@/chat/mediaAttachments';
import { theme } from '@/theme';

type Props = {
  attachments: ComposerMediaAttachment[];
  onRemoveAttachment?: (attachmentId: string) => void;
};

export function ComposerMediaTray({ attachments, onRemoveAttachment }: Props) {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <View style={styles.container}>
      <Text style={styles.label}>
        {attachments.length} {attachments.length === 1 ? 'photo' : 'photos'} ready for /event
      </Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
        {attachments.map((attachment) => (
          <View key={attachment.attachmentId} style={styles.card}>
            <Image source={{ uri: attachment.uri }} style={styles.image} resizeMode="cover" />
            <View style={styles.captionRow}>
              <Text numberOfLines={1} style={styles.caption}>
                {attachment.fileName}
              </Text>
              {onRemoveAttachment ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Remove ${attachment.fileName}`}
                  onPress={() => onRemoveAttachment(attachment.attachmentId)}
                  style={({ pressed }) => [styles.removeButton, pressed && styles.removeButtonPressed]}
                >
                  <Ionicons name="close" size={14} color={theme.colors.ink} />
                </Pressable>
              ) : null}
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 8,
  },
  label: {
    fontSize: 12,
    lineHeight: 16,
    color: theme.colors.mutedInk,
    fontWeight: '600',
  },
  row: {
    gap: 10,
    paddingRight: 4,
  },
  card: {
    width: 88,
    gap: 6,
  },
  image: {
    width: 88,
    height: 88,
    borderRadius: 14,
    backgroundColor: '#fff',
  },
  captionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  caption: {
    flex: 1,
    fontSize: 11,
    lineHeight: 14,
    color: theme.colors.ink,
  },
  removeButton: {
    width: 18,
    height: 18,
    borderRadius: 9,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f2ece5',
  },
  removeButtonPressed: {
    opacity: 0.75,
  },
});
