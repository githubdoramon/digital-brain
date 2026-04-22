import React from 'react';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import {
  ActivityIndicator,
  Animated,
  FlatList,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { Avatar } from '@/components/Avatar';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { EntityFilterSheet } from '@/components/entities/EntityFilterSheet';
import {
  buildActiveFilterChips,
  buildFilterOptionMaps,
  countActiveFilters,
  formatDocumentDate,
  formatDocumentSubtitle,
  formatEventDate,
  formatEventFilterDescription,
  formatPlaceSubtitle,
} from '@/components/entities/helpers';
import {
  ENTITY_META,
  EMPTY_ENTITY_FILTERS,
  type ContactListItem,
  type DocumentCollectionResponse,
  type DocumentListItem,
  type EntityFilterOption,
  type EntityFilters,
  type EntityKind,
  type EventListItem,
  type EventSearchResponse,
  type PlaceListItem,
} from '@/components/entities/types';
import { RelationshipChips } from '@/components/RelationshipChips';
import { theme } from '@/theme';

const EVENT_PAGE_SIZE = 30;
type EntityListRow = ContactListItem | PlaceListItem | EventListItem | DocumentListItem;

function appendIds(searchParams: URLSearchParams, key: string, values: string[]) {
  for (const value of values) {
    searchParams.append(key, value);
  }
}

function EntityChip({ kind, selected, onPress }: { kind: EntityKind; selected: boolean; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.entityChip, selected && styles.entityChipSelected, pressed && styles.entityChipPressed]}
    >
      <Ionicons name={ENTITY_META[kind].icon} size={16} color={selected ? '#fff' : theme.colors.mutedInk} />
      <Text style={[styles.entityChipText, selected && styles.entityChipTextSelected]}>{ENTITY_META[kind].label}</Text>
    </Pressable>
  );
}

function ContactCard({
  contact,
  contactMap,
  token,
  filterSelected,
  onToggleFilter,
  onPress,
}: {
  contact: ContactListItem;
  contactMap: Map<string, string>;
  token: string | null;
  filterSelected: boolean;
  onToggleFilter: () => void;
  onPress: () => void;
}) {
  const subtitle = contact.emails?.[0] || contact.phones?.[0] || 'No primary contact info yet';
  const chips = (contact.relationships || []).slice(0, 3).map((relationship) => ({
    label: `${relationship.type} · ${contactMap.get(relationship.contact_id) ?? 'Unknown'}`,
  }));

  return (
    <Card variant="elevated">
      <View style={styles.rowShell}>
        <Pressable onPress={onPress} style={styles.contactCardTapArea}>
          <Avatar name={contact.display_name} uri={contact.avatar_url ?? undefined} token={token} />
          <View style={styles.cardBody}>
            <Text style={styles.cardTitle}>{contact.display_name}</Text>
            <Text style={styles.cardSubtitle}>{subtitle}</Text>
            <RelationshipChips chips={chips} />
          </View>
        </Pressable>
        <Pressable
          onPress={onToggleFilter}
          accessibilityRole="button"
          accessibilityLabel={filterSelected ? 'Remove contact from filter' : 'Add contact to filter'}
          style={({ pressed }) => [styles.filterToggleButton, filterSelected && styles.filterToggleButtonActive, pressed && styles.filterToggleButtonPressed]}
        >
          <Ionicons
            name={filterSelected ? 'remove-circle' : 'add-circle-outline'}
            size={20}
            color={filterSelected ? theme.colors.teal : theme.colors.mutedInk}
          />
        </Pressable>
      </View>
    </Card>
  );
}

function PlaceCard({
  place,
  filterSelected,
  onToggleFilter,
  onPress,
}: {
  place: PlaceListItem;
  filterSelected: boolean;
  onToggleFilter: () => void;
  onPress: () => void;
}) {
  return (
    <Card style={styles.simpleCard}>
      <View style={styles.rowShell}>
        <Pressable onPress={onPress} style={styles.simpleCardTapArea}>
          <View style={styles.cardBody}>
            <Text style={styles.cardTitle}>{place.name?.trim() || place.place_id}</Text>
            <Text style={styles.cardSubtitle}>{formatPlaceSubtitle(place)}</Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color={theme.colors.mutedInk} />
        </Pressable>
        <Pressable
          onPress={onToggleFilter}
          accessibilityRole="button"
          accessibilityLabel={filterSelected ? 'Remove place from filter' : 'Add place to filter'}
          style={({ pressed }) => [styles.filterToggleButton, filterSelected && styles.filterToggleButtonActive, pressed && styles.filterToggleButtonPressed]}
        >
          <Ionicons
            name={filterSelected ? 'remove-circle' : 'add-circle-outline'}
            size={20}
            color={filterSelected ? theme.colors.teal : theme.colors.mutedInk}
          />
        </Pressable>
      </View>
    </Card>
  );
}

function EventCard({
  event,
  filterSelected,
  onToggleFilter,
  onPress,
}: {
  event: EventListItem;
  filterSelected: boolean;
  onToggleFilter: () => void;
  onPress: () => void;
}) {
  return (
    <Card style={styles.simpleCard}>
      <View style={styles.rowShell}>
        <Pressable onPress={onPress} style={styles.simpleCardTapArea}>
          <View style={styles.cardBody}>
            <Text style={styles.cardTitle}>{String(event.title || '').trim() || 'Untitled event'}</Text>
            <Text style={styles.cardSubtitle}>{formatEventDate(event.start_date)}</Text>
            {event.summary ? (
              <Text style={styles.cardMeta} numberOfLines={2}>
                {event.summary}
              </Text>
            ) : null}
          </View>
          <Ionicons name="chevron-forward" size={18} color={theme.colors.mutedInk} />
        </Pressable>
        <Pressable
          onPress={onToggleFilter}
          accessibilityRole="button"
          accessibilityLabel={filterSelected ? 'Remove event from filter' : 'Add event to filter'}
          style={({ pressed }) => [styles.filterToggleButton, filterSelected && styles.filterToggleButtonActive, pressed && styles.filterToggleButtonPressed]}
        >
          <Ionicons
            name={filterSelected ? 'remove-circle' : 'add-circle-outline'}
            size={20}
            color={filterSelected ? theme.colors.teal : theme.colors.mutedInk}
          />
        </Pressable>
      </View>
    </Card>
  );
}

function DocumentCard({ document, onPress }: { document: DocumentListItem; onPress: () => void }) {
  return (
    <Card style={styles.simpleCard}>
      <Pressable onPress={onPress} style={styles.simpleCardTapArea}>
        <View style={styles.cardBody}>
          <Text style={styles.cardTitle}>{document.title?.trim() || document.file_name || 'Untitled document'}</Text>
          <Text style={styles.cardSubtitle}>{formatDocumentDate(document.document_date)}</Text>
          <Text style={styles.cardMeta} numberOfLines={2}>
            {formatDocumentSubtitle(document)}
          </Text>
        </View>
        <Ionicons name="chevron-forward" size={18} color={theme.colors.mutedInk} />
      </Pressable>
    </Card>
  );
}

export default function EntitiesScreen() {
  const { token, name, email, photo } = useAuth();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const tabBarHeight = useBottomTabBarHeight();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const hasLoadedOnceRef = React.useRef<Record<EntityKind, boolean>>({
    contacts: false,
    places: false,
    events: false,
    documents: false,
  });
  const requestVersionRef = React.useRef(0);
  const eventNextOffsetRef = React.useRef(0);
  const eventHasMoreRef = React.useRef(false);
  const eventLoadingMoreRef = React.useRef(false);

  const [selectedEntity, setSelectedEntity] = React.useState<EntityKind>('contacts');
  const [queries, setQueries] = React.useState<Record<EntityKind, string>>({
    contacts: '',
    places: '',
    events: '',
    documents: '',
  });
  const [filters, setFilters] = React.useState<EntityFilters>(EMPTY_ENTITY_FILTERS);
  const [showFilterSheet, setShowFilterSheet] = React.useState(false);
  const [contacts, setContacts] = React.useState<ContactListItem[]>([]);
  const [places, setPlaces] = React.useState<PlaceListItem[]>([]);
  const [events, setEvents] = React.useState<EventListItem[]>([]);
  const [documents, setDocuments] = React.useState<DocumentListItem[]>([]);
  const [filterOptions, setFilterOptions] = React.useState<EntityFilterOption[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [isRefreshing, setIsRefreshing] = React.useState(false);
  const [isLoadingMore, setIsLoadingMore] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [focusTick, setFocusTick] = React.useState(0);

  const filterSignature = React.useMemo(() => JSON.stringify(filters), [filters]);
  const query = queries[selectedEntity];

  const contactMap = React.useMemo(() => {
    const map = new Map<string, string>();
    contacts.forEach((contact) => map.set(contact.contact_id, contact.display_name));
    return map;
  }, [contacts]);

  const combinedFilterOptions = React.useMemo(() => {
    const optionsByKey = new Map<string, EntityFilterOption>();
    for (const option of filterOptions) {
      optionsByKey.set(`${option.kind}:${option.id}`, option);
    }
    for (const contact of contacts) {
      optionsByKey.set(`contacts:${contact.contact_id}`, {
        id: contact.contact_id,
        kind: 'contacts',
        label: contact.display_name,
        description:
          contact.emails?.[0] || contact.phones?.[0] || contact.tags?.slice(0, 2).join(' • ') || null,
      });
    }
    for (const place of places) {
      optionsByKey.set(`places:${place.place_id}`, {
        id: place.place_id,
        kind: 'places',
        label: place.name?.trim() || place.place_id,
        description: formatPlaceSubtitle(place),
      });
    }
    for (const event of events) {
      optionsByKey.set(`events:${event.id}`, {
        id: event.id,
        kind: 'events',
        label: String(event.title || '').trim() || 'Untitled event',
        description: formatEventFilterDescription(event),
      });
    }
    for (const document of documents) {
      optionsByKey.set(`documents:${document.document_id}`, {
        id: document.document_id,
        kind: 'documents',
        label: document.title?.trim() || document.file_name || 'Untitled document',
        description: formatDocumentSubtitle(document),
      });
    }
    return Array.from(optionsByKey.values());
  }, [contacts, documents, events, filterOptions, places]);
  const optionMap = React.useMemo(() => buildFilterOptionMaps(combinedFilterOptions), [combinedFilterOptions]);
  const activeFilterChips = React.useMemo(() => buildActiveFilterChips(filters, optionMap), [filters, optionMap]);
  const activeFilterCount = React.useMemo(() => countActiveFilters(filters), [filters]);
  const listData = React.useMemo<EntityListRow[]>(() => {
    if (selectedEntity === 'contacts') return contacts;
    if (selectedEntity === 'places') return places;
    if (selectedEntity === 'documents') return documents;
    return events;
  }, [contacts, documents, events, places, selectedEntity]);

  const loadFilterOptions = React.useCallback(async () => {
    try {
      const [contactsResult, placesResult, eventsResult] = await Promise.all([
        apiFetch('/mobile/contacts'),
        apiFetch('/mobile/places?limit=500'),
        apiFetch('/mobile/events/search?limit=50'),
      ]);
      const nextOptions: EntityFilterOption[] = [];
      const contactItems = ((contactsResult as { contacts?: ContactListItem[] }).contacts || []) as ContactListItem[];
      const placeItems = ((placesResult as { places?: PlaceListItem[] }).places || []) as PlaceListItem[];
      const eventItems = ((eventsResult as EventSearchResponse).events || []) as EventListItem[];

      contactItems.forEach((contact) => {
        nextOptions.push({
          id: contact.contact_id,
          kind: 'contacts',
          label: contact.display_name,
          description: contact.emails?.[0] || contact.phones?.[0] || contact.tags?.slice(0, 2).join(' • ') || null,
        });
      });
      placeItems.forEach((place) => {
        nextOptions.push({
          id: place.place_id,
          kind: 'places',
          label: place.name?.trim() || place.place_id,
          description: formatPlaceSubtitle(place),
        });
      });
      eventItems.forEach((event) => {
        nextOptions.push({
          id: event.id,
          kind: 'events',
          label: String(event.title || '').trim() || 'Untitled event',
          description: formatEventFilterDescription(event),
        });
      });
      setFilterOptions(nextOptions);
    } catch (error) {
      console.warn('[entities] filter options load failed', error);
    }
  }, []);

  const isFilterSelected = React.useCallback(
    (kind: EntityKind, id: string) => {
      if (kind === 'contacts') return filters.contactIds.includes(id);
      if (kind === 'places') return filters.placeIds.includes(id);
      if (kind === 'documents') return false;
      return filters.eventIds.includes(id);
    },
    [filters.contactIds, filters.eventIds, filters.placeIds],
  );

  const toggleFilter = React.useCallback((kind: EntityKind, id: string) => {
    setFilters((current) => {
      if (kind === 'documents') {
        return current;
      }
      const key = kind === 'contacts' ? 'contactIds' : kind === 'places' ? 'placeIds' : 'eventIds';
      const nextValues = current[key].includes(id)
        ? current[key].filter((value) => value !== id)
        : current[key].concat(id);
      return {
        ...current,
        [key]: nextValues,
      };
    });
  }, []);

  const buildCommonSearchParams = React.useCallback(() => {
    const searchParams = new URLSearchParams();
    const trimmed = query.trim();
    if (trimmed) {
      searchParams.set('query', trimmed);
    }
    if (selectedEntity !== 'contacts') {
      appendIds(searchParams, 'contact_ids', filters.contactIds);
    }
    if (selectedEntity !== 'places') {
      appendIds(searchParams, 'place_ids', filters.placeIds);
    }
    if (selectedEntity !== 'events') {
      appendIds(searchParams, 'event_ids', filters.eventIds);
    }
    return searchParams;
  }, [filters.contactIds, filters.eventIds, filters.placeIds, query, selectedEntity]);

  const loadContacts = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false } = {}) => {
      const requestId = ++requestVersionRef.current;
      if (showInitialLoader) setIsLoading(true);
      if (showRefreshSpinner) setIsRefreshing(true);
      setLoadError(null);
      try {
        const searchParams = buildCommonSearchParams();
        const path = searchParams.toString() ? `/mobile/contacts?${searchParams.toString()}` : '/mobile/contacts';
        const result = (await apiFetch(path)) as { contacts?: ContactListItem[] };
        if (requestId !== requestVersionRef.current) return;
        setContacts(result.contacts ?? []);
      } catch (error) {
        if (requestId !== requestVersionRef.current) return;
        console.warn('[entities] contacts load failed', error);
        setLoadError('Unable to load contacts. Pull to refresh.');
      } finally {
        if (requestId === requestVersionRef.current) {
          setIsLoading(false);
          setIsRefreshing(false);
        }
      }
    },
    [buildCommonSearchParams],
  );

  const loadPlaces = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false } = {}) => {
      const requestId = ++requestVersionRef.current;
      if (showInitialLoader) setIsLoading(true);
      if (showRefreshSpinner) setIsRefreshing(true);
      setLoadError(null);
      try {
        const searchParams = buildCommonSearchParams();
        searchParams.set('limit', '500');
        if (query.trim()) {
          searchParams.set('q', query.trim());
          searchParams.delete('query');
        }
        const result = (await apiFetch(`/mobile/places?${searchParams.toString()}`)) as { places?: PlaceListItem[] };
        if (requestId !== requestVersionRef.current) return;
        setPlaces(result.places ?? []);
      } catch (error) {
        if (requestId !== requestVersionRef.current) return;
        console.warn('[entities] places load failed', error);
        setLoadError('Unable to load places. Pull to refresh.');
      } finally {
        if (requestId === requestVersionRef.current) {
          setIsLoading(false);
          setIsRefreshing(false);
        }
      }
    },
    [buildCommonSearchParams, query],
  );

  const loadEvents = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false, append = false } = {}) => {
      if (append && (eventLoadingMoreRef.current || !eventHasMoreRef.current)) {
        return;
      }

      const requestId = ++requestVersionRef.current;
      const offset = append ? eventNextOffsetRef.current : 0;
      if (append) {
        eventLoadingMoreRef.current = true;
        setIsLoadingMore(true);
      } else {
        eventNextOffsetRef.current = 0;
        eventHasMoreRef.current = false;
      }
      if (showInitialLoader) setIsLoading(true);
      if (showRefreshSpinner) setIsRefreshing(true);
      setLoadError(null);

      try {
        const searchParams = buildCommonSearchParams();
        searchParams.set('limit', String(EVENT_PAGE_SIZE));
        searchParams.set('offset', String(offset));
        const result = (await apiFetch(`/mobile/events/search?${searchParams.toString()}`)) as EventSearchResponse;
        if (requestId !== requestVersionRef.current) return;
        const incomingEvents = result.events ?? [];
        setEvents((current) => {
          if (!append) return incomingEvents;
          const seen = new Set(current.map((event) => event.id));
          return current.concat(incomingEvents.filter((event) => !seen.has(event.id)));
        });
        eventNextOffsetRef.current = typeof result.next_offset === 'number' ? result.next_offset : offset + incomingEvents.length;
        eventHasMoreRef.current = Boolean(result.has_more);
      } catch (error) {
        if (requestId !== requestVersionRef.current) return;
        console.warn('[entities] events load failed', error);
        setLoadError('Unable to load events. Pull to refresh.');
      } finally {
        if (requestId === requestVersionRef.current) {
          setIsLoading(false);
          setIsRefreshing(false);
          setIsLoadingMore(false);
          eventLoadingMoreRef.current = false;
        }
      }
    },
    [buildCommonSearchParams],
  );

  const loadDocuments = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false } = {}) => {
      const requestId = ++requestVersionRef.current;
      if (showInitialLoader) setIsLoading(true);
      if (showRefreshSpinner) setIsRefreshing(true);
      setLoadError(null);
      try {
        let result: DocumentCollectionResponse;
        if (query.trim()) {
          result = (await apiFetch('/mobile/documents/search', {
            method: 'POST',
            body: JSON.stringify({ query: query.trim(), limit: 50 }),
          })) as DocumentCollectionResponse;
        } else {
          result = (await apiFetch('/mobile/documents?limit=100')) as DocumentCollectionResponse;
        }
        if (requestId !== requestVersionRef.current) return;
        setDocuments(result.documents ?? []);
      } catch (error) {
        if (requestId !== requestVersionRef.current) return;
        console.warn('[entities] documents load failed', error);
        setLoadError('Unable to load documents. Pull to refresh.');
      } finally {
        if (requestId === requestVersionRef.current) {
          setIsLoading(false);
          setIsRefreshing(false);
        }
      }
    },
    [query],
  );

  const loadCurrentEntity = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false, append = false } = {}) => {
      if (selectedEntity === 'contacts') {
        await loadContacts({ showInitialLoader, showRefreshSpinner });
        return;
      }
      if (selectedEntity === 'places') {
        await loadPlaces({ showInitialLoader, showRefreshSpinner });
        return;
      }
      if (selectedEntity === 'documents') {
        await loadDocuments({ showInitialLoader, showRefreshSpinner });
        return;
      }
      await loadEvents({ showInitialLoader, showRefreshSpinner, append });
    },
    [loadContacts, loadDocuments, loadEvents, loadPlaces, selectedEntity],
  );

  useFocusEffect(
    React.useCallback(() => {
      setFocusTick((current) => current + 1);
      void loadFilterOptions();
      return undefined;
    }, [loadFilterOptions]),
  );

  React.useEffect(() => {
    const shouldShowInitialLoader = !hasLoadedOnceRef.current[selectedEntity];
    const timeout = setTimeout(() => {
      void loadCurrentEntity({ showInitialLoader: shouldShowInitialLoader });
      hasLoadedOnceRef.current[selectedEntity] = true;
    }, 180);
    return () => clearTimeout(timeout);
  }, [focusTick, filterSignature, loadCurrentEntity, selectedEntity]);

  const handleRefresh = React.useCallback(() => {
    void loadCurrentEntity({ showRefreshSpinner: true });
  }, [loadCurrentEntity]);

  const handleRemoveActiveFilter = React.useCallback((kind: EntityKind, id: string) => {
    toggleFilter(kind, id);
  }, [toggleFilter]);

  const listHeader = (
    <View style={styles.header}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.entityChipRow}>
        {(['contacts', 'places', 'events', 'documents'] as const).map((kind) => (
          <EntityChip key={kind} kind={kind} selected={selectedEntity === kind} onPress={() => setSelectedEntity(kind)} />
        ))}
      </ScrollView>

      <View style={styles.searchRow}>
        <View style={styles.searchInputWrap}>
          <Ionicons name="search-outline" size={18} color={theme.colors.mutedInk} />
          <TextInput
            value={query}
            onChangeText={(value) =>
              setQueries((current) => ({
                ...current,
                [selectedEntity]: value,
              }))
            }
            placeholder={ENTITY_META[selectedEntity].placeholder}
            placeholderTextColor={theme.colors.mutedInk}
            style={styles.searchInput}
          />
          {query ? (
            <Pressable
              onPress={() =>
                setQueries((current) => ({
                  ...current,
                  [selectedEntity]: '',
                }))
              }
              accessibilityRole="button"
              accessibilityLabel="Clear search"
              style={({ pressed }) => [styles.clearSearchButton, pressed && styles.clearSearchButtonPressed]}
            >
              <Ionicons name="close-circle" size={18} color={theme.colors.mutedInk} />
            </Pressable>
          ) : null}
        </View>
        <Pressable
          onPress={() => setShowFilterSheet(true)}
          accessibilityRole="button"
          accessibilityLabel="Open filters"
          style={({ pressed }) => [styles.filterButton, activeFilterCount > 0 && styles.filterButtonActive, pressed && styles.filterButtonPressed]}
        >
          <Ionicons name="options-outline" size={20} color={activeFilterCount > 0 ? theme.colors.teal : theme.colors.ink} />
          {activeFilterCount ? (
            <View style={styles.filterBadge}>
              <Text style={styles.filterBadgeText}>{activeFilterCount}</Text>
            </View>
          ) : null}
        </Pressable>
      </View>

    </View>
  );

  return (
    <View style={styles.container}>
      <FlatList<EntityListRow>
        data={listData}
        key={selectedEntity}
        keyExtractor={(item) => {
          if ('contact_id' in item) return item.contact_id;
          if ('place_id' in item) return item.place_id;
          if ('document_id' in item) return item.document_id;
          return item.id;
        }}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], { useNativeDriver: false })}
        scrollEventThrottle={16}
        refreshing={isRefreshing}
        onRefresh={handleRefresh}
        onEndReachedThreshold={selectedEntity === 'events' ? 0.35 : undefined}
        onEndReached={selectedEntity === 'events' ? () => void loadEvents({ append: true }) : undefined}
        progressViewOffset={insets.top + COLLAPSING_TOP_BAR_HEIGHT + 16}
        contentContainerStyle={[
          styles.listContent,
          {
            paddingTop: insets.top + COLLAPSING_TOP_BAR_HEIGHT + COLLAPSING_CONTENT_TOP_PADDING,
            paddingBottom: insets.bottom + tabBarHeight + 122,
          },
        ]}
        ListHeaderComponent={listHeader}
        ListEmptyComponent={
          isLoading ? (
            <ActivityIndicator size="small" color={theme.colors.accent} style={styles.loader} />
          ) : loadError ? (
            <Text style={[styles.empty, styles.errorText]}>{loadError}</Text>
          ) : (
            <Text style={styles.empty}>No {ENTITY_META[selectedEntity].label.toLowerCase()} found.</Text>
          )
        }
        ListFooterComponent={
          isLoadingMore ? <ActivityIndicator size="small" color={theme.colors.accent} style={styles.loader} /> : null
        }
        renderItem={({ item }) => {
          if (selectedEntity === 'contacts') {
            const contact = item as ContactListItem;
            return (
              <ContactCard
                contact={contact}
                contactMap={contactMap}
                token={token}
                filterSelected={isFilterSelected('contacts', contact.contact_id)}
                onToggleFilter={() => toggleFilter('contacts', contact.contact_id)}
                onPress={() =>
                  router.push({
                    pathname: '/contacts/[contactId]',
                    params: { contactId: contact.contact_id },
                  })
                }
              />
            );
          }

          if (selectedEntity === 'places') {
            const place = item as PlaceListItem;
            return (
              <PlaceCard
                place={place}
                filterSelected={isFilterSelected('places', place.place_id)}
                onToggleFilter={() => toggleFilter('places', place.place_id)}
                onPress={() =>
                  router.push({
                    pathname: '/places/[placeId]',
                    params: { placeId: place.place_id },
                  })
                }
              />
            );
          }

          if (selectedEntity === 'documents') {
            const document = item as DocumentListItem;
            return (
              <DocumentCard
                document={document}
                onPress={() =>
                  router.push({
                    pathname: '/documents/[documentId]/index',
                    params: { documentId: document.document_id },
                  })
                }
              />
            );
          }

          const event = item as EventListItem;
          return (
            <EventCard
              event={event}
              filterSelected={isFilterSelected('events', event.id)}
              onToggleFilter={() => toggleFilter('events', event.id)}
              onPress={() =>
                router.push({
                  pathname: '/events/[eventId]',
                  params: { eventId: event.id },
                })
              }
            />
          );
        }}
      />

      <CollapsingTopBar
        title="Entities"
        secondaryTitle="Skim through your entities"
        scrollY={scrollY}
        profileName={name || email || 'You'}
        profilePhoto={photo}
        token={token}
        onPressProfile={() => router.push('/settings')}
      />

      {selectedEntity === 'places' ? (
        <Pressable
          onPress={() => router.push('/places/new')}
          accessibilityRole="button"
          accessibilityLabel="Create place"
          style={({ pressed }) => [styles.fab, { bottom: insets.bottom + tabBarHeight + 24 }, pressed && styles.fabPressed]}
        >
          <Ionicons name="add" size={24} color="#fff" />
        </Pressable>
      ) : null}

      <EntityFilterSheet
        visible={showFilterSheet}
        chips={activeFilterChips}
        onApply={setFilters}
        onRemove={handleRemoveActiveFilter}
        onClose={() => setShowFilterSheet(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  listContent: {
    paddingHorizontal: 20,
    gap: 14,
  },
  header: {
    paddingTop: COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
    paddingBottom: 12,
    gap: 12,
  },
  entityChipRow: {
    gap: 10,
    paddingRight: 20,
  },
  entityChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: 'rgba(255,255,255,0.78)',
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  entityChipSelected: {
    borderColor: theme.colors.accentDeep,
    backgroundColor: theme.colors.accentDeep,
  },
  entityChipPressed: {
    opacity: 0.82,
  },
  entityChipText: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  entityChipTextSelected: {
    color: '#fff',
  },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  searchInputWrap: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 2,
  },
  searchInput: {
    flex: 1,
    paddingVertical: 12,
    color: theme.colors.ink,
  },
  clearSearchButton: {
    marginLeft: 4,
    paddingVertical: 6,
    paddingLeft: 4,
  },
  clearSearchButtonPressed: {
    opacity: 0.7,
  },
  filterButton: {
    width: 48,
    height: 48,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
  },
  filterButtonActive: {
    borderColor: theme.colors.teal,
    backgroundColor: '#f4faf9',
  },
  filterButtonPressed: {
    opacity: 0.84,
  },
  filterBadge: {
    position: 'absolute',
    top: 7,
    right: 7,
    minWidth: 18,
    height: 18,
    borderRadius: 9,
    paddingHorizontal: 4,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
  },
  filterBadgeText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#fff',
  },
  contactCardTapArea: {
    flexDirection: 'row',
    gap: 14,
    padding: 16,
    borderRadius: theme.radius.lg,
    flex: 1,
  },
  simpleCard: {
    padding: 0,
    overflow: 'hidden',
  },
  rowShell: {
    flexDirection: 'row',
    alignItems: 'stretch',
  },
  simpleCardTapArea: {
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 14,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    flex: 1,
  },
  filterToggleButton: {
    width: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderLeftWidth: 1,
    borderLeftColor: theme.colors.line,
    backgroundColor: 'rgba(255,255,255,0.7)',
  },
  filterToggleButtonActive: {
    backgroundColor: '#f4faf9',
  },
  filterToggleButtonPressed: {
    opacity: 0.82,
  },
  cardBody: {
    flex: 1,
    gap: 5,
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
  cardMeta: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    marginTop: 1,
  },
  empty: {
    fontSize: 14,
    color: theme.colors.mutedInk,
    textAlign: 'center',
    marginTop: 22,
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
    shadowOpacity: 0.32,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 8,
  },
  fabPressed: {
    transform: [{ scale: 0.97 }],
    shadowOpacity: 0.18,
  },
});
