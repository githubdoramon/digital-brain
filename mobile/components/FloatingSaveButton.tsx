import React, { useEffect, useRef } from 'react';
import { ActivityIndicator, Animated, StyleSheet } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type FloatingSaveButtonProps = {
  visible: boolean;
  label?: string;
  onPress: () => void;
  disabled?: boolean;
  loading?: boolean;
  bottomOffset?: number;
};

export function FloatingSaveButton({
  visible,
  label = 'Save changes',
  onPress,
  disabled,
  loading = false,
  bottomOffset = 20,
}: FloatingSaveButtonProps) {
  const translateY = useRef(new Animated.Value(40)).current;
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(translateY, {
        toValue: visible ? 0 : 40,
        duration: 220,
        useNativeDriver: true,
      }),
      Animated.timing(opacity, {
        toValue: visible ? 1 : 0,
        duration: 180,
        useNativeDriver: true,
      }),
    ]).start();
  }, [visible, opacity, translateY]);

  return (
    <Animated.View
      style={[
        styles.container,
        { bottom: bottomOffset },
        { transform: [{ translateY }], opacity },
      ]}
      pointerEvents={visible ? 'auto' : 'none'}
    >
      <Pressable
        accessibilityLabel={label}
        style={[styles.button, disabled && styles.disabled]}
        onPress={onPress}
        disabled={disabled}
      >
        {loading ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Ionicons name="checkmark" size={26} color="#fff" />
        )}
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    right: 20,
    shadowColor: '#0f1113',
    shadowOpacity: 0.36,
    shadowRadius: 22,
    shadowOffset: { width: 0, height: 16 },
    elevation: 14,
  },
  button: {
    backgroundColor: theme.colors.accent,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#0f1113',
    shadowOpacity: 0.12,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
  disabled: {
    opacity: 0.6,
  },
});
