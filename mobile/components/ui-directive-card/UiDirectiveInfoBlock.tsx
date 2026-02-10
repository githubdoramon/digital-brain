import React from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import type { UiDirectiveBlock } from '@/chat/uiDirectives';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

type Props = {
  block: UiDirectiveBlock;
};

async function openLink(url: string) {
  try {
    await Linking.openURL(url);
  } catch {
    // Ignore - URL validity is enforced server-side.
  }
}

export function UiDirectiveInfoBlock({ block }: Props) {
  return (
    <View style={styles.infoWrap}>
      {block.body ? <Text style={styles.description}>{block.body}</Text> : null}
      {(block.links || []).map((link) => (
        <Pressable
          key={`${block.id}:${link.url}`}
          accessibilityRole="link"
          accessibilityLabel={link.label}
          onPress={() => {
            void openLink(link.url);
          }}
          style={({ pressed }) => [pressed && styles.linkButtonPressed]}
        >
          <Card variant="surface" style={styles.linkButton}>
            <View style={styles.linkRow}>
              <View style={styles.linkTextWrap}>
                <Text style={styles.linkText}>{link.label}</Text>
                <Text style={styles.linkUrl} numberOfLines={1}>
                  {link.url}
                </Text>
              </View>
              <Ionicons name="open-outline" size={16} color={theme.colors.accentDeep} />
            </View>
          </Card>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  infoWrap: {
    gap: 8,
  },
  description: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 21,
  },
  linkButton: {
    minHeight: 48,
    paddingHorizontal: 12,
    paddingVertical: 10,
    justifyContent: 'center',
  },
  linkButtonPressed: {
    opacity: 0.78,
  },
  linkRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  linkTextWrap: {
    flexShrink: 1,
    gap: 2,
  },
  linkText: {
    color: theme.colors.accentDeep,
    fontSize: 14,
    lineHeight: 19,
    fontWeight: '700',
  },
  linkUrl: {
    color: theme.colors.mutedInk,
    fontSize: 12,
    lineHeight: 16,
  },
});
