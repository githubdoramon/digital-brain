import React from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import type { UiDirectiveBlock } from '@/chat/uiDirectives';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

type Props = {
  block: UiDirectiveBlock;
};

/** True if the part before ":" is 1–3 words (we bold it). */
function isLabelValueLine(line: string, separatorIndex: number): boolean {
  const beforeColon = line.slice(0, separatorIndex).trim();
  const words = beforeColon.split(/\s+/).filter(Boolean);
  return words.length >= 1 && words.length <= 3;
}

function renderBodyLine(line: string, key: string) {
  const separatorIndex = line.indexOf(':');
  if (separatorIndex <= 0 || !isLabelValueLine(line, separatorIndex)) {
    return (
      <Text key={key} style={styles.description}>
        {line}
      </Text>
    );
  }

  const rawLabel = line.slice(0, separatorIndex).trim();
  const value = line.slice(separatorIndex + 1).trimStart();

  return (
    <Text key={key} style={styles.description}>
      <Text style={styles.descriptionStrong}>{rawLabel}: </Text>
      {value}
    </Text>
  );
}

async function openLink(url: string) {
  try {
    await Linking.openURL(url);
  } catch {
    // Ignore - URL validity is enforced server-side.
  }
}

export function UiDirectiveInfoBlock({ block }: Props) {
  const bodyLines = (block.body || '').split('\n');

  return (
    <View style={styles.infoWrap}>
      {block.body
        ? bodyLines.map((line, index) =>
            renderBodyLine(line, `${block.id}-line-${index}`),
          )
        : null}
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
  descriptionStrong: {
    color: theme.colors.ink,
    fontWeight: '700',
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
