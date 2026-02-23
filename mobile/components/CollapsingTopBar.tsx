import { HeaderBackButton } from '@react-navigation/elements';
import React from 'react';
import { Animated, Platform, StyleSheet, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Avatar } from '@/components/Avatar';
import { theme } from '@/theme';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

type CollapsingTopBarProps = {
  title: string;
  secondaryTitle?: string;
  scrollY: Animated.Value;
  profileName?: string;
  profilePhoto?: string | null;
  token?: string | null;
  onPressProfile?: () => void;
  onPressBack?: () => void;
};

export const COLLAPSING_TOP_BAR_HEIGHT = 48;
export const COLLAPSING_EXPANDED_TITLE_TOP_OFFSET = 0;
export const COLLAPSING_CONTENT_TOP_PADDING = 0;
export const COLLAPSING_SECONDARY_TITLE_TOP_OFFSET = 12;
export const COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT = 58;

const COLLAPSED_TITLE_LINE_HEIGHT = 24;

export function CollapsingTopBar({
  title,
  secondaryTitle,
  scrollY,
  profileName,
  profilePhoto,
  token,
  onPressProfile,
  onPressBack,
}: CollapsingTopBarProps) {
  const insets = useSafeAreaInsets();
  const collapsedTop =
    insets.top + Math.round((COLLAPSING_TOP_BAR_HEIGHT - COLLAPSED_TITLE_LINE_HEIGHT ) / 2) - (Platform.OS === 'ios' ? 2 : 0);
  const hasBack = Boolean(onPressBack);
  const hasProfile = Boolean(onPressProfile);
  const expandedLeft = 20;
  const collapsedLeft = hasBack ? 52 : 20;
  const collapsedRight = hasProfile ? 80 : 20;
  const travelDistance = insets.top + COLLAPSING_TOP_BAR_HEIGHT - collapsedTop - 8;
  const collapseProgress = scrollY.interpolate({
    inputRange: [0, Math.max(64, travelDistance + 28)],
    outputRange: [0, 1],
    extrapolate: 'clamp',
  });

  const titleSize = collapseProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [12, 17],
  });
  const titleColor = collapseProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [theme.colors.teal, theme.colors.ink],
  });
  const titleLetterSpacing = collapseProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [2.6, 0.2],
  });
  const titleTranslateY = collapseProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [travelDistance, 0],
  });
  const titleTranslateX = collapseProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [expandedLeft - collapsedLeft, 0],
  });
  const barOpacity = collapseProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 0.96],
  });
  const secondaryOpacity = scrollY.interpolate({
    inputRange: [0, 42],
    outputRange: [1, 0],
    extrapolate: 'clamp',
  });
  const secondaryTranslateY = scrollY.interpolate({
    inputRange: [0, 42],
    outputRange: [0, -10],
    extrapolate: 'clamp',
  });

  return (
    <View pointerEvents="box-none" style={styles.wrap}>
      <Animated.View
        pointerEvents="none"
        style={[
          styles.backdrop,
          {
            height: insets.top + COLLAPSING_TOP_BAR_HEIGHT,
            opacity: barOpacity,
          },
        ]}
      />

      <Animated.Text
        numberOfLines={1}
        style={[
          styles.collapsedTitle,
          {
            top: collapsedTop,
            left: collapsedLeft,
            right: collapsedRight,
            fontSize: titleSize,
            color: titleColor,
            letterSpacing: titleLetterSpacing,
            transform: [{ translateX: titleTranslateX }, { translateY: titleTranslateY }],
          },
        ]}
      >
        {title}
      </Animated.Text>

      {secondaryTitle ? (
        <Animated.Text
          numberOfLines={1}
          style={[
            styles.secondaryTitle,
            {
              top: insets.top + COLLAPSING_TOP_BAR_HEIGHT + COLLAPSING_SECONDARY_TITLE_TOP_OFFSET,
              opacity: secondaryOpacity,
              transform: [{ translateY: secondaryTranslateY }],
            },
          ]}
        >
          {secondaryTitle}
        </Animated.Text>
      ) : null}

      {hasBack ? (
        <View style={[styles.leftActionWrap, { top: insets.top + 4 }]}> 
          <HeaderBackButton
            onPress={onPressBack}
            tintColor={theme.colors.ink}
            displayMode="minimal"
            style={styles.backButton}
          />
        </View>
      ) : null}

      {hasProfile ? (
        <View style={[styles.rightActionWrap, { top: insets.top + 4 }]}> 
          <Pressable
            onPress={onPressProfile}
            accessibilityRole="button"
            accessibilityLabel="Open profile settings"
            style={({ pressed }) => [styles.avatarButton, pressed && styles.buttonPressed]}
          >
            <Avatar name={profileName || 'You'} uri={profilePhoto} token={token} size={32} />
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 5,
  },
  backdrop: {
    backgroundColor: 'rgba(247, 242, 236, 0.92)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(231, 222, 212, 0.95)',
  },
  collapsedTitle: {
    position: 'absolute',
    fontSize: 12,
    lineHeight: COLLAPSED_TITLE_LINE_HEIGHT,
    fontWeight: '700',
    color: theme.colors.teal,
    letterSpacing: 2.6,
    textTransform: 'uppercase',
  },
  secondaryTitle: {
    position: 'absolute',
    left: 20,
    right: 20,
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  leftActionWrap: {
    position: 'absolute',
    left: 20,
  },
  rightActionWrap: {
    position: 'absolute',
    right: 20,
  },
  backButton: {
    marginLeft: -6,
  },
  avatarButton: {
    minHeight: 40,
    minWidth: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: 'rgba(255, 255, 255, 0.92)',
    padding: 3,
  },
  buttonPressed: {
    opacity: 0.75,
  },
});
