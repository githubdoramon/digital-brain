import Ionicons from '@expo/vector-icons/Ionicons';
import { useHeaderHeight } from '@react-navigation/elements';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';
import type { Place } from '@/types/place';
import { normalizeSearch } from '@/utils/text';

function formatSubtitle(place: Place): string {
  const description = (place.description || '').trim();
  if (description) {
    return description;
  }
  const pieces = [place.address, place.city, place.country]
    .map((value) => (value || '').trim())
    .filter(Boolean);
  if (pieces.length > 0) return pieces.join(' • ');
  if (typeof place.lat === 'number' && typeof place.lon === 'number') {
    return `${place.lat.toFixed(4)}, ${place.lon.toFixed(4)}`;
  }
  return 'No location details yet';
}

export default function SettingsPlacesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const headerHeight = useHeaderHeight();
  const [places, setPlaces] = useState<Place[]>([]);
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const hasLoadedOnceRef = useRef(false);

  useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showSubscription = Keyboard.addListener(showEvent, (event) => {
      setKeyboardVisible(true);
      setKeyboardHeight(event.endCoordinates?.height ?? 0);
    });
    const hideSubscription = Keyboard.addListener(hideEvent, () => {
      setKeyboardVisible(false);
      setKeyboardHeight(0);
    });
    return () => {
      showSubscription.remove();
      hideSubscription.remove();
    };
  }, []);

  const loadPlaces = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false } = {}) => {
      const refreshStartedAt = showRefreshSpinner ? Date.now() : null;
      if (showInitialLoader) {
        setIsLoading(true);
      }
      if (showRefreshSpinner) {
        setIsRefreshing(true);
      }
      try {
        setLoadError(null);
        const result = (await apiFetch('/mobile/places?limit=500')) as { places: Place[] };
        setPlaces(result.places ?? []);
      } catch (error) {
        console.warn('[places] load failed', error);
        setLoadError('Unable to load places. Pull to refresh.');
      } finally {
        if (refreshStartedAt !== null) {
          const elapsed = Date.now() - refreshStartedAt;
          const minVisibleMs = 450;
          if (elapsed < minVisibleMs) {
            await new Promise((resolve) => setTimeout(resolve, minVisibleMs - elapsed));
          }
        }
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [],
  );

  useFocusEffect(
    React.useCallback(() => {
      void loadPlaces({ showInitialLoader: !hasLoadedOnceRef.current });
      hasLoadedOnceRef.current = true;
      return undefined;
    }, [loadPlaces]),
  );

  const handleRefresh = React.useCallback(() => {
    void loadPlaces({ showRefreshSpinner: true });
  }, [loadPlaces]);

  const filtered = useMemo(() => {
    const trimmed = normalizeSearch(query.trim());
    if (!trimmed) return places;
    return places.filter((place) => {
      const haystack = [place.name, place.address, place.city, place.country, ...(place.aliases || [])]
        .filter(Boolean)
        .join(' ');
      return normalizeSearch(haystack).includes(trimmed);
    });
  }, [places, query]);

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={headerHeight}
    >
      <FlatList
        data={filtered}
        keyExtractor={(item) => item.place_id}
        refreshing={isRefreshing}
        onRefresh={handleRefresh}
        automaticallyAdjustKeyboardInsets
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: Platform.OS === 'android' ? 12 : headerHeight + 12,
            paddingBottom: insets.bottom + (keyboardVisible ? keyboardHeight + 24 : 110),
          },
        ]}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>Places</Text>
            <Text style={styles.subtitle}>Edit known places and keep their coordinates accurate.</Text>
            <TextInput
              value={query}
              onChangeText={setQuery}
              placeholder="Search places"
              placeholderTextColor={theme.colors.mutedInk}
              style={styles.searchInput}
            />
          </View>
        }
        ListEmptyComponent={
          isLoading ? (
            <ActivityIndicator size="small" color={theme.colors.accent} style={styles.loader} />
          ) : loadError ? (
            <Text style={[styles.empty, styles.errorText]}>{loadError}</Text>
          ) : (
            <Text style={styles.empty}>No places found.</Text>
          )
        }
        renderItem={({ item }) => (
          <Card style={styles.card}>
            <Pressable
              style={styles.cardTapArea}
              onPress={() =>
                router.push({
                  pathname: '/places/[placeId]',
                  params: { placeId: item.place_id },
                })
              }
            >
              <View style={styles.cardBody}>
                <Text style={styles.cardTitle}>{item.name?.trim() || item.place_id}</Text>
                <Text style={styles.cardSubtitle}>{formatSubtitle(item)}</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={theme.colors.mutedInk} />
            </Pressable>
          </Card>
        )}
      />

      {!keyboardVisible ? (
        <Pressable
          onPress={() => router.push('/places/new')}
          accessibilityRole="button"
          accessibilityLabel="Create place"
          style={({ pressed }) => [
            styles.fab,
            { bottom: insets.bottom + 26 },
            pressed && styles.fabPressed,
          ]}
        >
          <Ionicons name="add" size={24} color="#fff" />
        </Pressable>
      ) : null}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  content: {
    paddingHorizontal: 20,
    gap: 14,
  },
  header: {
    gap: 10,
    marginBottom: 8,
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  searchInput: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: theme.colors.ink,
  },
  card: {
    padding: 0,
  },
  cardTapArea: {
    paddingHorizontal: 14,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  cardBody: {
    flex: 1,
    gap: 4,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  cardSubtitle: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  empty: {
    fontSize: 14,
    textAlign: 'center',
    marginTop: 18,
    color: theme.colors.mutedInk,
  },
  errorText: {
    color: theme.colors.accent,
  },
  loader: {
    marginTop: 24,
  },
  fab: {
    position: 'absolute',
    right: 22,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: '#0f1113',
    shadowOpacity: 0.3,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 8,
  },
  fabPressed: {
    transform: [{ scale: 0.97 }],
    shadowOpacity: 0.16,
  },
});
