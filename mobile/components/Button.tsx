import React from 'react';
import { ActivityIndicator, Animated, Easing, StyleSheet, Text, View, ViewStyle } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type ButtonVariant = 'primary' | 'secondary' | 'clear' | 'danger';

type ButtonProps = {
  label: string;
  variant?: ButtonVariant;
  disabled?: boolean;
  loading?: boolean;
  loadingLabel?: string;
  onPress: () => void;
  style?: ViewStyle;
};

function LoadingDots({ color }: { color: string }) {
  const values = React.useRef([
    new Animated.Value(0.35),
    new Animated.Value(0.35),
    new Animated.Value(0.35),
  ]).current;

  React.useEffect(() => {
    const animation = Animated.loop(
      Animated.stagger(
        120,
        values.map((value) =>
          Animated.sequence([
            Animated.timing(value, {
              toValue: 1,
              duration: 280,
              easing: Easing.out(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.timing(value, {
              toValue: 0.35,
              duration: 280,
              easing: Easing.in(Easing.quad),
              useNativeDriver: true,
            }),
          ]),
        ),
      ),
    );

    animation.start();
    return () => {
      animation.stop();
    };
  }, [values]);

  return (
    <View style={styles.loadingDotsRow}>
      {values.map((value, index) => (
        <Animated.View
          key={index}
          style={[
            styles.loadingDot,
            {
              backgroundColor: color,
              opacity: value,
              transform: [
                {
                  translateY: value.interpolate({
                    inputRange: [0.35, 1],
                    outputRange: [0, -1.5],
                  }),
                },
              ],
            },
          ]}
        />
      ))}
    </View>
  );
}

const rippleByVariant: Record<ButtonVariant, string> = {
  primary: 'rgba(255,255,255,0.2)',
  secondary: 'rgba(0,0,0,0.08)',
  clear: 'rgba(0,0,0,0.08)',
  danger: 'rgba(192,57,43,0.12)',
};

const labelColorByVariant: Record<ButtonVariant, string> = {
  primary: '#fff',
  secondary: theme.colors.ink,
  clear: theme.colors.ink,
  danger: '#c0392b',
};

export function Button({ label, variant = 'primary', disabled, loading = false, loadingLabel, onPress, style }: ButtonProps) {
  const isDisabled = disabled || loading;
  const labelColor = labelColorByVariant[variant];

  return (
    <View
      style={[
        styles.base,
        styles[variant],
        isDisabled && styles.disabled,
        loading && styles.loading,
        style,
      ]}
    >
      <Pressable
        onPress={onPress}
        disabled={isDisabled}
        android_ripple={{ color: rippleByVariant[variant], borderless: false }}
        style={styles.pressable}
      >
        <View style={styles.contentRow}>
          {loading ? <ActivityIndicator size="small" color={labelColor} /> : null}
          <Text style={[styles.label, styles[`${variant}Label` as const]]}>{loading ? loadingLabel || label : label}</Text>
          {loading ? <LoadingDots color={labelColor} /> : null}
        </View>
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
  contentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
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
  danger: {
    backgroundColor: '#fdf4f3',
    borderWidth: 1,
    borderColor: '#e8c4c0',
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
  dangerLabel: {
    color: '#c0392b',
  },
  disabled: {
    opacity: 0.6,
  },
  loading: {
    opacity: 0.92,
  },
  loadingDotsRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 4,
  },
  loadingDot: {
    width: 5,
    height: 5,
    borderRadius: 999,
  },
});
