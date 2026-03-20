import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import MapView, { Marker, PROVIDER_GOOGLE, Region } from 'react-native-maps';
import * as Location from 'expo-location';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { LinkedContactsCard } from '@/components/place/LinkedContactsCard';
import { theme } from '@/theme';
import type { Place } from '@/types/place';
import { openNativeMapForPlace } from '@/utils/maps';
import { normalizeRouteParam } from '@/utils/text';

type Draft = {
  place_id: string;
  name: string;
  aliasesText: string;
  description: string;
  address: string;
  city: string;
  country: string;
  latText: string;
  lonText: string;
};

const DEFAULT_REGION: Region = {
  latitude: 37.7749,
  longitude: -122.4194,
  latitudeDelta: 0.04,
  longitudeDelta: 0.04,
};

function toAliases(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function toNumberOrNull(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatReverseAddress(
  reverse: Location.LocationGeocodedAddress | null | undefined,
): string {
  if (!reverse) return '';
  const lineOne = [reverse.name, reverse.street].filter(Boolean).join(' ').trim();
  const lineTwo = [reverse.city, reverse.region, reverse.postalCode].filter(Boolean).join(', ').trim();
  const country = (reverse.country || '').trim();
  return [lineOne, lineTwo, country].filter(Boolean).join(' • ').trim();
}

function buildPlaceId(draft: Draft): string {
  const source = draft.name.trim() || draft.address.trim() || draft.city.trim() || 'place';
  const slug = source
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return `place-${slug || 'new'}-${Date.now().toString(36)}`;
}

function draftFromPlace(place: Place): Draft {
  return {
    place_id: place.place_id,
    name: place.name || '',
    aliasesText: (place.aliases || []).join(', '),
    description: place.description || '',
    address: place.address || '',
    city: place.city || '',
    country: place.country || '',
    latText: typeof place.lat === 'number' ? String(place.lat) : '',
    lonText: typeof place.lon === 'number' ? String(place.lon) : '',
  };
}

function getRegionFromDraft(draft: Draft): Region {
  const lat = toNumberOrNull(draft.latText);
  const lon = toNumberOrNull(draft.lonText);
  if (lat === null || lon === null) return DEFAULT_REGION;
  return {
    latitude: lat,
    longitude: lon,
    latitudeDelta: 0.01,
    longitudeDelta: 0.01,
  };
}

function floatingOffset(insetBottom: number, keyboardHeight: number) {
  const keyboardInset =
    Platform.OS === 'ios' ? Math.max(0, keyboardHeight - insetBottom) : keyboardHeight;
  return insetBottom + 20 + keyboardInset;
}

export function PlaceEditorScreen({ placeId }: { placeId?: string }) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const normalizedPlaceId = normalizeRouteParam(placeId);
  const isCreating = !normalizedPlaceId;
  const mapRef = useRef<MapView | null>(null);
  const [initial, setInitial] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isLocating, setIsLocating] = useState(false);
  const [isGeocodingAddress, setIsGeocodingAddress] = useState(false);
  const [isLoading, setIsLoading] = useState(!isCreating);
  const [isMapReady, setIsMapReady] = useState(false);
  const [mapKey, setMapKey] = useState(0);
  const [keyboardHeight, setKeyboardHeight] = useState(0);

  useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillChangeFrame' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showListener = Keyboard.addListener(showEvent, (event) => {
      setKeyboardHeight(Math.max(0, event.endCoordinates?.height ?? 0));
    });
    const hideListener = Keyboard.addListener(hideEvent, () => {
      setKeyboardHeight(0);
    });

    return () => {
      showListener.remove();
      hideListener.remove();
    };
  }, []);

  useEffect(() => {
    if (isCreating) {
      const empty: Draft = {
        place_id: '',
        name: '',
        aliasesText: '',
        description: '',
        address: '',
        city: '',
        country: '',
        latText: '',
        lonText: '',
      };
      setInitial(empty);
      setDraft(empty);
      setIsLoading(false);
      return;
    }

    let mounted = true;
    void (async () => {
      setIsLoading(true);
      try {
        const result = (await apiFetch(
          `/mobile/places/${encodeURIComponent(normalizedPlaceId)}`,
        )) as Place;
        if (!mounted) return;
        const normalized = draftFromPlace(result);
        setInitial(normalized);
        setDraft(normalized);
      } catch (error) {
        console.warn('[places] detail load failed', error);
        if (mounted) {
          Alert.alert('Load failed', 'Unable to load place details. Please go back and try again.');
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, [isCreating, normalizedPlaceId]);

  const isDirty = useMemo(() => {
    if (!draft || !initial) return false;
    return JSON.stringify(draft) !== JSON.stringify(initial);
  }, [draft, initial]);

  const markerCoordinate = useMemo(() => {
    if (!draft) return null;
    const lat = toNumberOrNull(draft.latText);
    const lon = toNumberOrNull(draft.lonText);
    if (lat === null || lon === null) return null;
    return { latitude: lat, longitude: lon };
  }, [draft]);

  const region = useMemo(() => {
    if (!draft) return DEFAULT_REGION;
    return getRegionFromDraft(draft);
  }, [draft]);

  useFocusEffect(
    useCallback(() => {
      if (Platform.OS === 'android') {
        setIsMapReady(false);
        setMapKey((current) => current + 1);
      }
      return undefined;
    }, []),
  );

  const syncMapToRegion = useCallback(
    (nextRegion: Region, animated: boolean) => {
      const map = mapRef.current;
      if (!map) return;
      map.animateToRegion(nextRegion, animated ? 250 : 0);
    },
    [],
  );

  useEffect(() => {
    if (!isMapReady) {
      return;
    }
    const timeout = setTimeout(() => {
      syncMapToRegion(region, !!markerCoordinate);
    }, Platform.OS === 'android' ? 120 : 0);
    return () => clearTimeout(timeout);
  }, [isMapReady, markerCoordinate, region, syncMapToRegion]);

  useEffect(() => {
    if (!draft || isLocating) {
      return;
    }
    const hasCoordinates =
      toNumberOrNull(draft.latText) !== null && toNumberOrNull(draft.lonText) !== null;
    if (hasCoordinates) {
      return;
    }

    let active = true;
    setIsLocating(true);
    void (async () => {
      try {
        const permission = await Location.requestForegroundPermissionsAsync();
        if (permission.status !== 'granted') {
          return;
        }
        const position = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        if (!active) return;
        const latitude = Number(position.coords.latitude.toFixed(6));
        const longitude = Number(position.coords.longitude.toFixed(6));
        setDraft((current) => {
          if (!current) return current;
          const alreadySet =
            toNumberOrNull(current.latText) !== null && toNumberOrNull(current.lonText) !== null;
          if (alreadySet) return current;
          return {
            ...current,
            latText: String(latitude),
            lonText: String(longitude),
          };
        });
      } catch {
        // Best effort location seed.
      } finally {
        if (active) {
          setIsLocating(false);
        }
      }
    })();

    return () => {
      active = false;
    };
  }, [draft, isLocating]);

  const handleSearchAddress = async () => {
    if (!draft) return;
    const addressQuery = draft.address.trim();
    if (!addressQuery) {
      Alert.alert('Address required', 'Type an address first.');
      return;
    }

    setIsGeocodingAddress(true);
    try {
      const results = await Location.geocodeAsync(addressQuery);
      if (!results || results.length === 0) {
        Alert.alert('No coordinates found', 'Try a more specific address.');
        return;
      }
      const first = results[0];
      const latitude = Number(first.latitude.toFixed(6));
      const longitude = Number(first.longitude.toFixed(6));

      let reverseCity = '';
      let reverseCountry = '';
      let reverseAddress = '';
      try {
        const reverseResults = await Location.reverseGeocodeAsync({ latitude, longitude });
        const reverse = reverseResults?.[0];
        reverseCity = reverse?.city || reverse?.subregion || reverse?.region || '';
        reverseCountry = reverse?.country || '';
        reverseAddress = formatReverseAddress(reverse);
      } catch {
        // Keep going with geocode coordinates even if reverse details fail.
      }

      setDraft((current) => {
        if (!current) return current;
        return {
          ...current,
          latText: String(latitude),
          lonText: String(longitude),
          address: reverseAddress || current.address,
          city: reverseCity || current.city,
          country: reverseCountry || current.country,
        };
      });
    } catch {
      Alert.alert('Search failed', 'Could not resolve this address right now.');
    } finally {
      setIsGeocodingAddress(false);
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setIsSaving(true);
    try {
      const place_id = isCreating ? buildPlaceId(draft) : normalizedPlaceId;
      const payload = {
        place_id,
        name: draft.name.trim() || null,
        aliases: toAliases(draft.aliasesText),
        description: draft.description.trim() || null,
        address: draft.address.trim() || null,
        city: draft.city.trim() || null,
        country: draft.country.trim() || null,
        lat: toNumberOrNull(draft.latText),
        lon: toNumberOrNull(draft.lonText),
        geohash: null,
      };
      await apiFetch('/mobile/ingest/place', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (isCreating) {
        router.replace({
          pathname: '/places/[placeId]',
          params: { placeId: place_id },
        });
        return;
      }

      const refreshed = (await apiFetch(`/mobile/places/${encodeURIComponent(place_id)}`)) as Place;
      const normalized = draftFromPlace(refreshed);
      setInitial(normalized);
      setDraft(normalized);
    } catch (error) {
      console.warn('[places] save failed', error);
      Alert.alert('Save failed', 'Unable to save this place. Please try again.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleOpenMapApp = async () => {
    if (!draft) return;
    const lat = toNumberOrNull(draft.latText);
    const lon = toNumberOrNull(draft.lonText);
    if (lat === null || lon === null) {
      Alert.alert('Coordinates required', 'Set latitude and longitude to open this place in Maps.');
      return;
    }

    const opened = await openNativeMapForPlace({
      lat,
      lon,
      address: draft.address,
      name: draft.name,
    });
    if (!opened) {
      Alert.alert('Unable to open Maps', 'No compatible maps app was found.');
    }
  };

  const handleDeletePlace = async () => {
    if (isCreating || !normalizedPlaceId || isDeleting) return;

    setIsDeleting(true);
    try {
      await apiFetch(`/mobile/places/${encodeURIComponent(normalizedPlaceId)}`, {
        method: 'DELETE',
      });
      router.replace('/settings/places');
    } catch (error) {
      console.warn('[places] delete failed', error);
      Alert.alert('Delete failed', 'Unable to delete this place right now.');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleConfirmDelete = () => {
    if (isCreating || !normalizedPlaceId || isDeleting) return;

    const detail = isDirty
      ? 'This will permanently remove the place and discard your unsaved edits.'
      : 'This will permanently remove the place.';
    Alert.alert('Delete place?', detail, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () => {
          void handleDeletePlace();
        },
      },
    ]);
  };

  if (!draft) {
    return (
      <View style={styles.container}>
        <View style={styles.emptyState}>
          <Text style={styles.emptyTitle}>{isLoading ? 'Loading place...' : 'Place unavailable'}</Text>
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={0}
    >
      <ScrollView
        automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'}
        contentContainerStyle={[
          styles.content,
          {
            paddingTop: insets.top + 56,
            paddingBottom: floatingOffset(insets.bottom, keyboardHeight) + 96,
          },
        ]}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
      >
        <Text style={styles.title}>{isCreating ? 'Create place' : 'Edit place'}</Text>
        <Text style={styles.subtitle}>Update details and adjust its map location.</Text>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Details</Text>
          <TextInput
            style={styles.input}
            value={draft.name}
            onChangeText={(value) => setDraft({ ...draft, name: value })}
            placeholder="Place name"
            placeholderTextColor={theme.colors.mutedInk}
          />
          <TextInput
            style={styles.input}
            value={draft.aliasesText}
            onChangeText={(value) => setDraft({ ...draft, aliasesText: value })}
            placeholder="aliases, comma separated"
            placeholderTextColor={theme.colors.mutedInk}
          />
          <TextInput
            style={[styles.input, styles.multilineInput]}
            value={draft.description}
            onChangeText={(value) => setDraft({ ...draft, description: value })}
            placeholder="Description / notes"
            placeholderTextColor={theme.colors.mutedInk}
            multiline
            textAlignVertical="top"
          />
          <TextInput
            style={styles.input}
            value={draft.city}
            onChangeText={(value) => setDraft({ ...draft, city: value })}
            placeholder="City"
            placeholderTextColor={theme.colors.mutedInk}
          />
          <TextInput
            style={styles.input}
            value={draft.country}
            onChangeText={(value) => setDraft({ ...draft, country: value })}
            placeholder="Country"
            placeholderTextColor={theme.colors.mutedInk}
          />
        </Card>

        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Coordinates</Text>
          <TextInput
            style={styles.input}
            value={draft.address}
            onChangeText={(value) => setDraft({ ...draft, address: value })}
            placeholder="Address"
            placeholderTextColor={theme.colors.mutedInk}
          />
          <View style={styles.actionRow}>
            <Pressable
              style={({ pressed }) => [
                styles.actionButton,
                styles.primaryActionButton,
                (isGeocodingAddress || !draft.address.trim()) && styles.actionButtonDisabled,
                pressed && styles.actionButtonPressed,
              ]}
              onPress={() => void handleSearchAddress()}
              disabled={isGeocodingAddress || !draft.address.trim()}
            >
              <Ionicons name="search" size={16} color="#fff" />
              <Text style={[styles.actionButtonText, styles.primaryActionButtonText]}>
                {isGeocodingAddress ? 'Searching...' : 'Find coordinates'}
              </Text>
            </Pressable>
            <Pressable
              style={({ pressed }) => [
                styles.actionButton,
                styles.secondaryActionButton,
                !markerCoordinate && styles.actionButtonDisabled,
                pressed && styles.actionButtonPressed,
              ]}
              onPress={() => void handleOpenMapApp()}
              disabled={!markerCoordinate}
            >
              <Ionicons name="navigate" size={16} color={theme.colors.ink} />
              <Text style={[styles.actionButtonText, styles.secondaryActionButtonText]}>
                Open in Maps
              </Text>
            </Pressable>
          </View>
          {isLocating ? <Text style={styles.helper}>Detecting your current location...</Text> : null}
          <View style={styles.coordinateRow}>
            <TextInput
              style={[styles.input, styles.coordinateInput]}
              value={draft.latText}
              keyboardType="numbers-and-punctuation"
              onChangeText={(value) => setDraft({ ...draft, latText: value })}
              placeholder="Latitude"
              placeholderTextColor={theme.colors.mutedInk}
            />
            <TextInput
              style={[styles.input, styles.coordinateInput]}
              value={draft.lonText}
              keyboardType="numbers-and-punctuation"
              onChangeText={(value) => setDraft({ ...draft, lonText: value })}
              placeholder="Longitude"
              placeholderTextColor={theme.colors.mutedInk}
            />
          </View>
          <Text style={styles.helper}>Drag the marker to adjust precise coordinates.</Text>
          <View style={styles.mapWrap}>
            <MapView
              key={Platform.OS === 'android' ? `place-map-${mapKey}` : 'place-map'}
              ref={mapRef}
              style={styles.map}
              initialRegion={region}
              onMapReady={() => {
                setIsMapReady(true);
                syncMapToRegion(region, false);
              }}
              showsBuildings
              showsIndoors
              showsCompass
              {...(Platform.OS === 'android' && { provider: PROVIDER_GOOGLE })}
            >
              {markerCoordinate ? (
                <Marker
                  coordinate={markerCoordinate}
                  draggable
                  onDragEnd={(event) => {
                    const { latitude, longitude } = event.nativeEvent.coordinate;
                    setDraft((current) => {
                      if (!current) return current;
                      return {
                        ...current,
                        latText: String(Number(latitude.toFixed(6))),
                        lonText: String(Number(longitude.toFixed(6))),
                      };
                    });
                  }}
                />
              ) : null}
            </MapView>
            {!markerCoordinate ? (
              <Pressable
                onPress={() =>
                  setDraft({
                    ...draft,
                    latText: String(Number(DEFAULT_REGION.latitude.toFixed(6))),
                    lonText: String(Number(DEFAULT_REGION.longitude.toFixed(6))),
                  })
                }
                style={styles.addMarkerButton}
              >
                <Text style={styles.addMarkerText}>Set initial marker</Text>
              </Pressable>
            ) : null}
          </View>
        </Card>

        {!isCreating && normalizedPlaceId ? <LinkedContactsCard placeId={normalizedPlaceId} /> : null}

        {!isCreating && normalizedPlaceId ? (
          <Card style={styles.deleteSection}>
            <Text style={styles.sectionTitle}>Danger zone</Text>
            <Text style={styles.deleteHint}>
              This removes the place from your directory and unlinks it from related records.
            </Text>
            <Button
              label={isDeleting ? 'Deleting...' : 'Delete place'}
              variant="danger"
              disabled={isDeleting}
              onPress={handleConfirmDelete}
            />
          </Card>
        ) : null}
      </ScrollView>

      <FloatingSaveButton
        visible={isDirty}
        label={isSaving ? 'Saving...' : isCreating ? 'Create place' : 'Save changes'}
        onPress={handleSave}
        disabled={isSaving}
        loading={isSaving}
        bottomOffset={floatingOffset(insets.bottom, keyboardHeight)}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  content: {
    paddingHorizontal: 20,
    gap: 16,
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
  section: {
    padding: 16,
    gap: 10,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: theme.colors.ink,
  },
  multilineInput: {
    minHeight: 90,
    paddingTop: 12,
  },
  coordinateRow: {
    flexDirection: 'row',
    gap: 8,
  },
  coordinateInput: {
    flex: 1,
  },
  actionRow: {
    flexDirection: 'row',
    gap: 8,
  },
  actionButton: {
    flex: 1,
    minHeight: 44,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 12,
  },
  primaryActionButton: {
    backgroundColor: '#1d2938',
    borderColor: '#1d2938',
  },
  secondaryActionButton: {
    backgroundColor: '#f4f7f9',
    borderColor: theme.colors.line,
  },
  actionButtonText: {
    fontSize: 13,
    fontWeight: '700',
  },
  primaryActionButtonText: {
    color: '#fff',
  },
  secondaryActionButtonText: {
    color: theme.colors.ink,
  },
  actionButtonDisabled: {
    opacity: 0.5,
  },
  actionButtonPressed: {
    opacity: 0.86,
  },
  helper: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  mapWrap: {
    borderRadius: theme.radius.lg,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: theme.colors.line,
    position: 'relative',
  },
  map: {
    height: 240,
    width: '100%',
  },
  deleteSection: {
    padding: 16,
    gap: 10,
  },
  deleteHint: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  addMarkerButton: {
    position: 'absolute',
    bottom: 12,
    alignSelf: 'center',
    backgroundColor: '#fff',
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  addMarkerText: {
    color: theme.colors.ink,
    fontSize: 12,
    fontWeight: '600',
  },
});
