import React from 'react';
import { Animated, StyleSheet, View } from 'react-native';

type ScrollHeaderBackdropProps = {
  height: number;
  opacity: number | Animated.AnimatedInterpolation<number>;
};

export function ScrollHeaderBackdrop({ height, opacity }: ScrollHeaderBackdropProps) {
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        styles.wrap,
        {
          height,
          opacity,
        },
      ]}
    >
      <View style={[styles.tint, { height: Math.max(28, height * 0.55) }]} />
      <View style={styles.fade} />
      <View style={styles.divider} />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 2,
  },
  tint: {
    backgroundColor: 'rgba(247, 242, 236, 0.98)',
  },
  fade: {
    flex: 1,
    backgroundColor: 'rgba(247, 242, 236, 0.64)',
  },
  divider: {
    height: 1,
    backgroundColor: 'rgba(224, 214, 203, 0.95)',
  },
});
