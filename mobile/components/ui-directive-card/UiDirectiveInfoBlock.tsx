import React from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import type { UiDirectiveBlock } from '@/chat/uiDirectives';
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
          style={({ pressed }) => [styles.linkButton, pressed && styles.linkButtonPressed]}
        >
          <Text style={styles.linkText}>{link.label}</Text>
          <Text style={styles.linkUrl} numberOfLines={1}>
            {link.url}
          </Text>
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
    lineHeight: 20,
  },
  linkButton: {
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 10,
    paddingVertical: 8,
    justifyContent: 'center',
  },
  linkButtonPressed: {
    backgroundColor: '#f8f6f2',
  },
  linkText: {
    color: theme.colors.accentDeep,
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '700',
  },
  linkUrl: {
    color: theme.colors.mutedInk,
    fontSize: 12,
    lineHeight: 16,
  },
});

