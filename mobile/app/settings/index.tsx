import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { theme } from '@/theme';

export default function SettingsScreen() {
  const router = useRouter();
  const { signOut } = useAuth();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;

  return (
    <LinearGradient
      colors={theme.gradients.sunrise}
      style={styles.container}
    >
      <Animated.ScrollView
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        contentContainerStyle={[
          styles.content,
          {
            paddingTop:
              insets.top +
              COLLAPSING_TOP_BAR_HEIGHT +
              COLLAPSING_CONTENT_TOP_PADDING +
              COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
            paddingBottom: insets.bottom + 24,
          },
        ]}
      >
        <Card style={[styles.card, styles.navCard]}>
          <Pressable
            style={styles.navRow}
            onPress={() => router.push('/settings/notifications')}
          >
            <View style={styles.textBlock}>
              <Text style={styles.rowTitle}>Notifications</Text>
              <Text style={styles.rowSubtitle}>
                Choose alerts per type and delivery channel.
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/about-me')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>About me</Text>
            <Text style={styles.rowSubtitle}>
              View and manage what your Brain has learned about you.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/news-topics')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>News topics</Text>
            <Text style={styles.rowSubtitle}>
              Manage tracked topics for your daily briefing news feed.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/places')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>Places</Text>
            <Text style={styles.rowSubtitle}>
              Browse and edit saved places and map coordinates.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>

      <Card style={[styles.card, styles.navCard]}>
        <Pressable
          style={styles.navRow}
          onPress={() => router.push('/settings/events')}
        >
          <View style={styles.textBlock}>
            <Text style={styles.rowTitle}>Events</Text>
            <Text style={styles.rowSubtitle}>
              Search, view, and edit events with linked contacts and places.
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={theme.colors.mutedInk} />
        </Pressable>
      </Card>


        <Button
          label="Sign out"
          onPress={signOut}
          variant="primary"
          style={styles.signOutButton}
        />
      </Animated.ScrollView>

      <CollapsingTopBar
        title="Settings"
        secondaryTitle="Control your Brain"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 24,
  },
  card: {
    borderRadius: theme.radius.xl,
    padding: 20,
  },
  textBlock: {
    flex: 1,
  },
  rowTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  rowSubtitle: {
    marginTop: 6,
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  navCard: {
    marginTop: 16,
    padding: 0,
    overflow: 'hidden',
  },
  navRow: {
    borderRadius: theme.radius.xl,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 20,
    gap: 12,
  },
  signOutButton: {
    marginTop: 20,
    alignSelf: 'stretch',
    borderRadius: theme.radius.md,
    paddingHorizontal: 18,
    backgroundColor: theme.colors.ink,
  },
});
