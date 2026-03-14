import React from 'react';
import { Animated, StyleSheet, View } from 'react-native';

type ScrollHeaderBackdropProps = {
  height: number;
  opacity: number | Animated.AnimatedInterpolation<number>;
  topAlpha?: number;
  bottomAlpha?: number;
};

function clampAlpha(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function ScrollHeaderBackdrop({
  height,
  opacity,
  topAlpha = 0.98,
  bottomAlpha = 0.84,
}: ScrollHeaderBackdropProps) {
  const topColor = `rgba(247, 242, 236, ${clampAlpha(topAlpha)})`;
  const bottomColor = `rgba(247, 242, 236, ${clampAlpha(bottomAlpha)})`;

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
      <View
        style={[
          styles.tint,
          {
            height: Math.max(28, height * 0.58),
            backgroundColor: topColor,
          },
        ]}
      />
      <View style={[styles.fade, { backgroundColor: bottomColor }]} />
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
  },
  tint: {
    backgroundColor: 'rgba(247, 242, 236, 0.98)',
  },
  fade: {
    flex: 1,
    backgroundColor: 'rgba(247, 242, 236, 0.84)',
  },
  divider: {
    height: 1,
    backgroundColor: 'rgba(224, 214, 203, 0.95)',
  },
});
