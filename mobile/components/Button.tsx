import React from 'react';
import { StyleSheet, Text, View, ViewStyle } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type ButtonVariant = 'primary' | 'secondary' | 'clear';

type ButtonProps = {
  label: string;
  variant?: ButtonVariant;
  disabled?: boolean;
  onPress: () => void;
  style?: ViewStyle;
};

const rippleByVariant: Record<ButtonVariant, string> = {
  primary: 'rgba(255,255,255,0.2)',
  secondary: 'rgba(0,0,0,0.08)',
  clear: 'rgba(0,0,0,0.08)',
};

export function Button({ label, variant = 'primary', disabled, onPress, style }: ButtonProps) {
  return (
    <View
      style={[
        styles.base,
        styles[variant],
        disabled && styles.disabled,
        style,
      ]}
    >
      <Pressable
        onPress={onPress}
        disabled={disabled}
        android_ripple={{ color: rippleByVariant[variant], borderless: false }}
        style={styles.pressable}
      >
        <Text style={[styles.label, styles[`${variant}Label` as const]]}>{label}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    borderRadius: theme.radius.md,
    overflow: 'hidden',
  },
  pressable: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
    paddingHorizontal: 18,
  },
  primary: {
    backgroundColor: theme.colors.accent,
  },
  secondary: {
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  clear: {
    backgroundColor: 'transparent',
  },
  label: {
    fontWeight: '600',
  },
  primaryLabel: {
    color: '#fff',
  },
  secondaryLabel: {
    color: theme.colors.ink,
  },
  clearLabel: {
    color: theme.colors.ink,
  },
  disabled: {
    opacity: 0.6,
  },
});
