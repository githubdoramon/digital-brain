import React from 'react';
import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { Tabs } from 'expo-router';
import { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { ActivityIndicator, Animated, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { theme } from '@/theme';
import Digibrain from '@/assets/images/digibrain.svg';
import { TopNoticeProvider } from '@/components/top-notice';

const leftTabs = [
  {
    name: 'index',
    label: 'Daily',
    icon: 'calendar-outline' as const,
  },
  {
    name: 'contacts',
    label: 'Contacts',
    icon: 'people-outline' as const,
  },
];

const chatRoute = 'brain';

function TabBar({ state, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  const currentRoute = state.routes[state.index]?.name;
  const indicatorX = React.useRef(new Animated.Value(0)).current;
  const indicatorWidth = React.useRef(new Animated.Value(0)).current;
  const tabLayouts = React.useRef<Record<string, { x: number; width: number }>>({});
  const indicatorReady = React.useRef(false);
  const isLeftTabActive = leftTabs.some((tab) => tab.name === currentRoute);
  const chatSwap = React.useRef(new Animated.Value(currentRoute === chatRoute ? 1 : 0)).current;

  const handlePress = (routeName: string, routeKey: string) => {
    const event = navigation.emit({
      type: 'tabPress',
      target: routeKey,
      canPreventDefault: true,
    });
    if (!event.defaultPrevented) {
      navigation.navigate(routeName);
    }
  };

  React.useEffect(() => {
    if (!currentRoute || !isLeftTabActive) return;
    const layout = tabLayouts.current[currentRoute];
    if (!layout) return;
    Animated.parallel([
      Animated.timing(indicatorX, {
        toValue: layout.x,
        duration: 220,
        useNativeDriver: false,
      }),
      Animated.timing(indicatorWidth, {
        toValue: layout.width,
        duration: 220,
        useNativeDriver: false,
      }),
    ]).start();
  }, [currentRoute, indicatorWidth, indicatorX]);

  React.useEffect(() => {
    const focused = currentRoute === chatRoute;
    Animated.timing(chatSwap, {
      toValue: focused ? 1 : 0,
      duration: 220,
      useNativeDriver: true,
    }).start();
  }, [currentRoute, chatSwap]);

  return (
    <View style={[styles.barWrap, { paddingBottom: Math.max(insets.bottom, 14) }]}>
      <View style={styles.leftGroup}>
        {isLeftTabActive ? (
          <Animated.View
            pointerEvents="none"
            style={[
              styles.leftIndicator,
              {
                transform: [{ translateX: indicatorX }],
                width: indicatorWidth,
              },
            ]}
          />
        ) : null}
        {leftTabs.map((tab) => {
          const route = state.routes.find((item) => item.name === tab.name);
          if (!route) return null;
          const focused = currentRoute === route.name;
          return (
            <Pressable
              key={route.key}
              onPress={() => handlePress(route.name, route.key)}
              onLayout={(event) => {
                const layout = event.nativeEvent.layout;
                tabLayouts.current[route.name] = { x: layout.x, width: layout.width };
                if (route.name !== currentRoute) return;
                if (!indicatorReady.current) {
                  indicatorX.setValue(layout.x);
                  indicatorWidth.setValue(layout.width);
                  indicatorReady.current = true;
                  return;
                }
                Animated.parallel([
                  Animated.timing(indicatorX, {
                    toValue: layout.x,
                    duration: 180,
                    useNativeDriver: false,
                  }),
                  Animated.timing(indicatorWidth, {
                    toValue: layout.width,
                    duration: 180,
                    useNativeDriver: false,
                  }),
                ]).start();
              }}
              accessibilityRole="button"
              accessibilityState={focused ? { selected: true } : {}}
              style={({ pressed }) => [
                styles.tabButton,
                focused && styles.tabButtonActive,
                pressed && styles.tabButtonPressed,
              ]}
            >
              <Ionicons
                name={tab.icon}
                size={22}
                color={focused ? theme.colors.accentDeep : theme.colors.mutedInk}
              />
              {focused ? <Text style={styles.tabLabel}>{tab.label}</Text> : null}
            </Pressable>
          );
        })}
      </View>
      {(() => {
        const chat = state.routes.find((route) => route.name === chatRoute);
        if (!chat) return null;
        const focused = currentRoute === chat.name;
        const sendEnabled = Boolean(chat.params?.sendEnabled);
        const isSending = Boolean(chat.params?.isSending);
        const sendDisabled = focused && !sendEnabled;
        const sendOpacity = focused ? 1 : 0;
        const idleOpacity = focused ? 0 : 1;
        const sendScale = chatSwap.interpolate({
          inputRange: [0, 1],
          outputRange: [0.92, 1],
          extrapolate: 'clamp',
        });
        const idleScale = chatSwap.interpolate({
          inputRange: [0, 1],
          outputRange: [1, 0.92],
          extrapolate: 'clamp',
        });
        const sendRotate = chatSwap.interpolate({
          inputRange: [0, 1],
          outputRange: ['-8deg', '0deg'],
          extrapolate: 'clamp',
        });
        const idleRotate = chatSwap.interpolate({
          inputRange: [0, 1],
          outputRange: ['0deg', '8deg'],
          extrapolate: 'clamp',
        });
        return (
          <Pressable
            onPress={() => handlePress(chat.name, chat.key)}
            accessibilityRole="button"
            accessibilityState={
              focused
                ? {
                    selected: true,
                    disabled: sendDisabled,
                  }
                : sendDisabled
                  ? { disabled: true }
                  : {}
            }
            disabled={sendDisabled}
            style={({ pressed }) => [
              styles.brainButton,
              focused && styles.brainButtonActive,
              sendDisabled && styles.brainButtonDisabled,
              pressed && styles.brainButtonPressed,
            ]}
          >
            <View style={styles.brainButtonGradient}>
              <Animated.View
                style={[
                  styles.brainButtonLayer,
                  {
                    opacity: sendOpacity,
                    transform: [{ scale: sendScale }, { rotate: sendRotate }],
                  },
                  sendDisabled && styles.brainButtonDisabled,
                ]}
              >
                <LinearGradient
                  colors={[theme.colors.accentDeep, theme.colors.accent]}
                  start={{ x: 0.2, y: 0 }}
                  end={{ x: 0.9, y: 1 }}
                  style={styles.brainButtonFill}
                >
                  {isSending && focused ? (
                    <ActivityIndicator size="small" color="#fff" />
                  ) : (
                    <Ionicons name="send" size={24} color="#fff" />
                  )}
                </LinearGradient>
              </Animated.View>
              <Animated.View
                style={[
                  styles.brainButtonLayer,
                  {
                    opacity: idleOpacity,
                    transform: [{ scale: idleScale }, { rotate: idleRotate }],
                  },
                ]}
              >
                <View style={[styles.brainButtonFill, styles.brainButtonIdle]}>
                  <Digibrain width={30} height={30} color={theme.colors.mutedInk} />
                </View>
              </Animated.View>
            </View>
          </Pressable>
        );
      })()}
    </View>
  );
}

export default function TabLayout() {
  return (
    <TopNoticeProvider>
      <Tabs
        screenOptions={{
          headerShown: false,
          tabBarHideOnKeyboard: true,
          tabBarStyle: {
            position: 'absolute',
            backgroundColor: 'transparent',
            borderTopWidth: 0,
          },
        }}
        tabBar={(props) => <TabBar {...props} />}
      >
        <Tabs.Screen name="index" options={{ title: 'Daily' }} />
        <Tabs.Screen name="brain" options={{ title: 'Chat' }} />
        <Tabs.Screen name="contacts" options={{ title: 'Contacts' }} />
      </Tabs>
    </TopNoticeProvider>
  );
}

const styles = StyleSheet.create({
  barWrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 18,
    paddingTop: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  leftGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.82)',
    borderRadius: 30,
    paddingVertical: 8,
    paddingHorizontal: 10,
    gap: 6,
    borderWidth: 1,
    borderColor: theme.colors.line,
    position: 'relative',
    overflow: 'hidden',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.08,
    shadowRadius: 12,
    shadowOffset: theme.shadow.offset,
    elevation: 3,
  },
  leftIndicator: {
    position: 'absolute',
    left: 0,
    top: 6,
    bottom: 6,
    borderRadius: 22,
    backgroundColor: 'rgba(231, 222, 212, 0.55)',
    zIndex: 0,
  },
  tabButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 22,
    zIndex: 1,
  },
  tabButtonActive: {
    backgroundColor: 'transparent',
  },
  tabButtonPressed: {
    opacity: 0.8,
  },
  tabLabel: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
    letterSpacing: 0.2,
  },
  brainButton: {
    borderRadius: 30,
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.2,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
  brainButtonActive: {
    shadowOpacity: 0.3,
  },
  brainButtonDisabled: {
    opacity: 0.5,
  },
  brainButtonPressed: {
    transform: [{ scale: 0.98 }],
  },
  brainButtonGradient: {
    width: 60,
    height: 60,
    borderRadius: 30,
    overflow: 'hidden',
    position: 'relative',
    alignItems: 'center',
    justifyContent: 'center',
  },
  brainButtonLayer: {
    position: 'absolute',
    width: 60,
    height: 60,
    alignItems: 'center',
    justifyContent: 'center',
  },
  brainButtonFill: {
    width: 60,
    height: 60,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 30,
  },
  brainButtonIdle: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
});
