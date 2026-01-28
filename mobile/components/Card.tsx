import React from 'react';
import { StyleSheet, View, ViewProps } from 'react-native';

import { theme } from '@/theme';

type CardVariant = 'default' | 'surface' | 'elevated';

type CardProps = ViewProps & {
  variant?: CardVariant;
};

export function Card({ variant = 'default', style, children, ...rest }: CardProps) {
  const variantStyle =
    variant === 'elevated'
      ? cardStyles.elevated
      : variant === 'surface'
      ? cardStyles.surface
      : cardStyles.base;
  return (
    <View {...rest} style={[cardStyles.base, variantStyle, style]}>
      {children}
    </View>
  );
}

export const cardStyles = StyleSheet.create({
  base: {
    borderRadius: theme.radius.lg,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  surface: {
    backgroundColor: theme.colors.background,
  },
  elevated: {
    shadowColor: theme.shadow.color,
    shadowOpacity: theme.shadow.opacity,
    shadowRadius: theme.shadow.radius,
    shadowOffset: theme.shadow.offset,
    elevation: 2,
  },
});
