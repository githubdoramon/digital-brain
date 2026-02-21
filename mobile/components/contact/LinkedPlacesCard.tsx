import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React, { useMemo, useState } from 'react';
import { Alert, StyleSheet, Text, TextInput, View } from 'react-native';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';
import type { Place } from '@/types/place';
import { normalizeSearch } from '@/utils/text';

function placeLabel(place: Place): string {
  const location = [place.city, place.country].filter(Boolean).join(', ');
  if (place.role?.trim()) {
    return `${place.role}${location ? ` • ${location}` : ''}`;
  }
  if (location) return location;
  if (place.address?.trim()) return place.address;
  return 'No metadata';
}

export function LinkedPlacesCard({ contactId }: { contactId: string }) {
  const router = useRouter();
  const [linkedPlaces, setLinkedPlaces] = useState<Place[]>([]);
  const [allPlaces, setAllPlaces] = useState<Place[]>([]);
  const [search, setSearch] = useState('');
  const [selectedPlaceId, setSelectedPlaceId] = useState<string | null>(null);
  const [role, setRole] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const load = React.useCallback(async () => {
    try {
      const [linkedResult, allResult] = await Promise.all([
        apiFetch(`/mobile/contacts/${encodeURIComponent(contactId)}/places`),
        apiFetch('/mobile/places?limit=500'),
      ]);
      setLinkedPlaces(((linkedResult as { places: Place[] }).places || []) as Place[]);
      setAllPlaces(((allResult as { places: Place[] }).places || []) as Place[]);
    } catch (error) {
      console.warn('[contact-places] load failed', error);
    }
  }, [contactId]);

  useFocusEffect(
    React.useCallback(() => {
      void load();
      return undefined;
    }, [load]),
  );

  const linkedIds = useMemo(() => new Set(linkedPlaces.map((item) => item.place_id)), [linkedPlaces]);

  const suggestions = useMemo(() => {
    const trimmed = normalizeSearch(search.trim());
    if (!trimmed) return [];
    return allPlaces
      .filter((item) => !linkedIds.has(item.place_id))
      .filter((item) => {
        const haystack = [item.name, item.address, item.city, item.country, ...(item.aliases || [])]
          .filter(Boolean)
          .join(' ');
        return normalizeSearch(haystack).includes(trimmed);
      })
      .slice(0, 8);
  }, [allPlaces, linkedIds, search]);

  const handleAdd = async () => {
    if (!selectedPlaceId) return;
    setIsSaving(true);
    try {
      await apiFetch(`/mobile/contacts/${encodeURIComponent(contactId)}/places`, {
        method: 'POST',
        body: JSON.stringify({
          place_id: selectedPlaceId,
          role: role.trim() || null,
          source: 'mobile_manual',
          confidence: 'high',
        }),
      });
      setSelectedPlaceId(null);
      setSearch('');
      setRole('');
      await load();
    } catch (error) {
      console.warn('[contact-places] add failed', error);
      Alert.alert('Failed to link place', 'Unable to add this place link. Please try again.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async (placeId: string) => {
    setIsSaving(true);
    try {
      await apiFetch(
        `/mobile/contacts/${encodeURIComponent(contactId)}/places/${encodeURIComponent(placeId)}`,
        {
          method: 'DELETE',
        },
      );
      await load();
    } catch (error) {
      console.warn('[contact-places] remove failed', error);
      Alert.alert('Failed to remove place', 'Unable to remove this place link. Please try again.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card style={styles.section}>
      <Text style={styles.sectionTitle}>Linked places</Text>
      {linkedPlaces.length === 0 ? <Text style={styles.muted}>No linked places yet.</Text> : null}
      {linkedPlaces.map((place) => (
        <View key={place.place_id} style={styles.row}>
          <Pressable
            style={styles.placeButton}
            onPress={() =>
              router.push({
                pathname: '/places/[placeId]',
                params: { placeId: place.place_id },
              })
            }
          >
            <View style={styles.placeTextWrap}>
              <Text style={styles.placeName}>{place.name || place.place_id}</Text>
              <Text style={styles.placeMeta}>{placeLabel(place)}</Text>
            </View>
          </Pressable>
          <Pressable onPress={() => void handleRemove(place.place_id)} disabled={isSaving}>
            <Ionicons name="close-circle" size={20} color={theme.colors.mutedInk} />
          </Pressable>
        </View>
      ))}

      <View style={styles.divider} />

      <TextInput
        style={styles.input}
        value={search}
        onChangeText={(value) => {
          setSearch(value);
          setSelectedPlaceId(null);
        }}
        placeholder="Search places"
        placeholderTextColor={theme.colors.mutedInk}
      />
      {suggestions.length > 0 ? (
        <View style={styles.suggestions}>
          {suggestions.map((item) => (
            <Pressable
              key={item.place_id}
              style={[
                styles.suggestion,
                selectedPlaceId === item.place_id && styles.suggestionActive,
              ]}
              onPress={() => {
                setSelectedPlaceId(item.place_id);
                setSearch(item.name || item.place_id);
              }}
            >
              <Text style={styles.suggestionText}>{item.name || item.place_id}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      <TextInput
        style={styles.input}
        value={role}
        onChangeText={setRole}
        placeholder="Role (home, office, etc)"
        placeholderTextColor={theme.colors.mutedInk}
      />

      <Pressable
        style={[styles.addButton, (!selectedPlaceId || isSaving) && styles.addButtonDisabled]}
        onPress={() => void handleAdd()}
        disabled={!selectedPlaceId || isSaving}
      >
        <Text style={styles.addButtonText}>{isSaving ? 'Saving...' : 'Add place link'}</Text>
      </Pressable>
    </Card>
  );
}

const styles = StyleSheet.create({
  section: {
    gap: 10,
    padding: 16,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  muted: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  placeButton: {
    flex: 1,
  },
  placeTextWrap: {
    gap: 2,
  },
  placeName: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  placeMeta: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  divider: {
    height: 1,
    backgroundColor: theme.colors.line,
    marginVertical: 4,
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
  suggestions: {
    gap: 6,
  },
  suggestion: {
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
  },
  suggestionActive: {
    borderColor: theme.colors.accent,
    backgroundColor: theme.colors.paleTeal,
  },
  suggestionText: {
    fontSize: 13,
    color: theme.colors.ink,
  },
  addButton: {
    alignSelf: 'flex-start',
    backgroundColor: theme.colors.ink,
    borderRadius: theme.radius.md,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  addButtonDisabled: {
    opacity: 0.5,
  },
  addButtonText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '600',
  },
});
