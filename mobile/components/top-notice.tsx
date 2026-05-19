import { LinearGradient } from 'expo-linear-gradient';
import React from 'react';
import { Animated, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { theme } from '@/theme';

type NoticeVariant = 'success' | 'info' | 'warning' | 'error';

type TopNoticeContextValue = {
  showNotice: (message: string, variant?: NoticeVariant) => void;
};

const TopNoticeContext = React.createContext<TopNoticeContextValue | null>(null);

type NoticeState = {
  message: string;
  visible: boolean;
  variant: NoticeVariant;
};

export function TopNoticeProvider({ children }: { children: React.ReactNode }) {
  const insets = useSafeAreaInsets();
  const [notice, setNotice] = React.useState<NoticeState>({
    message: '',
    visible: false,
    variant: 'info',
  });
  const translateY = React.useRef(new Animated.Value(-60)).current;
  const opacity = React.useRef(new Animated.Value(0)).current;

  const showNotice = React.useCallback(
    (message: string, variant: NoticeVariant = 'info') => {
      setNotice({ message, visible: true, variant });
      translateY.setValue(-40);
      opacity.setValue(0);
      Animated.sequence([
        Animated.parallel([
          Animated.timing(translateY, {
            toValue: 0,
            duration: 200,
            useNativeDriver: true,
          }),
          Animated.timing(opacity, {
            toValue: 1,
            duration: 200,
            useNativeDriver: true,
          }),
        ]),
        Animated.delay(2000),
        Animated.parallel([
          Animated.timing(translateY, {
            toValue: -40,
            duration: 200,
            useNativeDriver: true,
          }),
          Animated.timing(opacity, {
            toValue: 0,
            duration: 200,
            useNativeDriver: true,
          }),
        ]),
      ]).start(() => {
        setNotice({ message: '', visible: false, variant });
      });
    },
    [opacity, translateY]
  );

  const contextValue = React.useMemo(() => ({ showNotice }), [showNotice]);

  const gradientColors = getGradientColors(notice.variant);

  return (
    <TopNoticeContext.Provider value={contextValue}>
      {children}
      {notice.visible ? (
        <Animated.View
          pointerEvents="none"
          style={[
            styles.noticeWrapper,
            {
              paddingTop: insets.top + 40,
              transform: [{ translateY }],
              opacity,
            },
          ]}
        >
          <LinearGradient
            colors={gradientColors}
            start={{ x: 0.1, y: 0 }}
            end={{ x: 0.9, y: 1 }}
            style={styles.notice}
          >
            <View style={styles.noticeGlow} />
            <Text style={styles.noticeText}>{notice.message}</Text>
          </LinearGradient>
        </Animated.View>
      ) : null}
    </TopNoticeContext.Provider>
  );
}

export function useTopNotice() {
  const context = React.useContext(TopNoticeContext);
  if (!context) {
    throw new Error('useTopNotice must be used within TopNoticeProvider');
  }
  return context;
}

function getGradientColors(variant: NoticeVariant) {
  switch (variant) {
    case 'success':
      return ['#1f9a6a', '#4bc27f'] as const;
    case 'warning':
      return ['#d98c1a', '#f1b541'] as const;
    case 'error':
      return ['#c63c3c', '#e15b5b'] as const;
    default:
      return [theme.colors.accentDeep, theme.colors.accent] as const;
  }
}

const styles = StyleSheet.create({
  noticeWrapper: {
    position: 'absolute',
    left: 16,
    right: 16,
    top: 0,
    zIndex: 40,
    elevation: 40,
    alignItems: 'center',
  },
  notice: {
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 10,
    width: '100%',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.2,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 4,
    overflow: 'hidden',
  },
  noticeGlow: {
    position: 'absolute',
    top: -30,
    right: -20,
    width: 120,
    height: 120,
    borderRadius: 60,
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
  },
  noticeText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#fff',
    textAlign: 'center',
  },
});
