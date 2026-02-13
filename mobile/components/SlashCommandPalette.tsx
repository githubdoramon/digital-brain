import React, { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type CommandOption = {
  command: string;
  description: string;
};

const COMMANDS: CommandOption[] = [
  { command: 'event', description: 'Log a new event or memory.' },
  { command: 'new', description: 'Start a fresh session.' },
];

type SlashCommandPaletteProps = {
  query: string;
  onSelect: (command: string) => void;
};

export function SlashCommandPalette({ query, onSelect }: SlashCommandPaletteProps) {
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return COMMANDS;
    return COMMANDS.filter((command) => command.command.startsWith(needle));
  }, [query]);

  if (filtered.length === 0) return null;

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Commands</Text>
      {filtered.map((command) => (
        <Pressable
          key={command.command}
          onPress={() => onSelect(command.command)}
          android_ripple={{ color: 'rgba(0,0,0,0.08)' }}
          style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
        >
          <View>
            <Text style={styles.command}>/{command.command}</Text>
            <Text style={styles.description}>{command.description}</Text>
          </View>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginHorizontal: 16,
    marginBottom: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
    shadowColor: theme.shadow.color,
    shadowOpacity: theme.shadow.opacity,
    shadowRadius: theme.shadow.radius,
    shadowOffset: theme.shadow.offset,
  },
  title: {
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 2,
    color: theme.colors.mutedInk,
    fontWeight: '600',
    marginBottom: 8,
  },
  row: {
    paddingVertical: 8,
  },
  rowPressed: {
    opacity: 0.7,
  },
  command: {
    fontSize: 15,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  description: {
    marginTop: 2,
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
});
