import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
  KeyboardAvoidingView,
  Platform,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { EventMediaSuggestionCard } from '@/components/event-draft/EventMediaSuggestionCard';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';
import { formatDraftDateTime, draftDateTimePickerValue } from '@/components/event-draft/dateTime';
import { UiDirectiveDateTimePickerSheet } from '@/components/ui-directive-card/UiDirectiveDateTimePickerSheet';
import type {
  EventContactOption,
  EventPhoto,
  EventPlaceOption,
} from '@/components/event-draft/types';
import { matchesContactSearch } from '@/utils/contactSearch';
import { normalizeSearch } from '@/utils/text';

type ProposedEvent = {
  proposal_id: string;
  status: 'pending' | 'accepted' | 'dismissed' | 'ignored' | 'expired';
  start_at: string;
  end_at: string;
  timezone?: string | null;
  duration_minutes: number;
  duration_label?: string | null;
  place_id?: string | null;
  place_name?: string | null;
  city?: string | null;
  country?: string | null;
  confidence: 'medium' | 'high';
  reason?: string | null;
  suggested_title?: string | null;
  suggested_summary?: string | null;
  suggested_contact_ids?: string[];
  place_candidates?: PlaceCandidate[];
  accepted_event_id?: string | null;
  event_id?: string | null;
  media_suggestions?: EventPhoto[];
};

type PlaceCandidate = {
  provider_place_id: string;
  title?: string | null;
  primary_type?: string | null;
  types?: string[];
  formatted_address?: string | null;
  distance_m?: number | null;
};

function formatTimeRange(startValue: string, endValue: string): string {
  const start = new Date(startValue);
  const end = new Date(endValue);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return 'Time unknown';
  }
  const date = start.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
  const startTime = start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  const endTime = end.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  return `${date}, ${startTime} - ${endTime}`;
}

function placeLabel(proposal: ProposedEvent): string {
  const name = proposal.place_name?.trim() || 'Unknown place';
  const locality = [proposal.city, proposal.country].filter(Boolean).join(', ');
  return locality ? `${name} · ${locality}` : name;
}

function todayPayload() {
  const now = new Date();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const targetDate = `${values.year}-${values.month}-${values.day}`;
  return { targetDate, timezone };
}

function proposalWallClockValue(value: string, timezoneName?: string | null): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezoneName || 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(parsed);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}

function placeOptionLabel(place: EventPlaceOption): string {
  return [place.name, place.city, place.country].filter(Boolean).join(' · ') || place.place_id;
}

export default function ProposedEventsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { token } = useAuth();
  const { showError, showSuccess } = useAppNotice();
  const [proposals, setProposals] = React.useState<ProposedEvent[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);
  const [runningScan, setRunningScan] = React.useState(false);
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [draftTitleById, setDraftTitleById] = React.useState<Record<string, string>>({});
  const [draftSummaryById, setDraftSummaryById] = React.useState<Record<string, string>>({});
  const [draftStartById, setDraftStartById] = React.useState<Record<string, string>>({});
  const [draftEndById, setDraftEndById] = React.useState<Record<string, string>>({});
  const [draftContactIdsById, setDraftContactIdsById] = React.useState<Record<string, string[]>>(
    {},
  );
  const [participantQueryById, setParticipantQueryById] = React.useState<Record<string, string>>(
    {},
  );
  const [draftPlaceIdById, setDraftPlaceIdById] = React.useState<Record<string, string | null>>({});
  const [draftPlaceTextById, setDraftPlaceTextById] = React.useState<Record<string, string>>({});
  const [selectedPlaceById, setSelectedPlaceById] = React.useState<Record<string, string | null>>(
    {},
  );
  const [selectedMediaIdsById, setSelectedMediaIdsById] = React.useState<Record<string, string[]>>(
    {},
  );
  const [availableContacts, setAvailableContacts] = React.useState<EventContactOption[]>([]);
  const [availablePlaces, setAvailablePlaces] = React.useState<EventPlaceOption[]>([]);
  const [activePicker, setActivePicker] = React.useState<{
    proposalId: string;
    field: 'start' | 'end';
  } | null>(null);
  const [savingId, setSavingId] = React.useState<string | null>(null);

  const loadProposals = React.useCallback(async () => {
    setLoading(true);
    try {
      const response = (await apiFetch('/mobile/proposed-events', { token })) as {
        proposals?: ProposedEvent[];
      };
      const next = Array.isArray(response?.proposals) ? response.proposals : [];
      setProposals(next);
      setDraftTitleById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = proposal.suggested_title || '';
          }
        });
        return merged;
      });
      setDraftSummaryById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = proposal.suggested_summary || '';
          }
        });
        return merged;
      });
      setDraftStartById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = proposalWallClockValue(
              proposal.start_at,
              proposal.timezone,
            );
          }
        });
        return merged;
      });
      setDraftEndById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = proposalWallClockValue(
              proposal.end_at,
              proposal.timezone,
            );
          }
        });
        return merged;
      });
      setDraftContactIdsById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = [...(proposal.suggested_contact_ids || [])];
          }
        });
        return merged;
      });
      setDraftPlaceIdById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (!Object.prototype.hasOwnProperty.call(merged, proposal.proposal_id)) {
            merged[proposal.proposal_id] = proposal.place_id || null;
          }
        });
        return merged;
      });
      setDraftPlaceTextById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = proposal.place_name || '';
          }
        });
        return merged;
      });
      setSelectedMediaIdsById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (merged[proposal.proposal_id] == null) {
            merged[proposal.proposal_id] = (proposal.media_suggestions || [])
              .filter((media) => media.status !== 'removed')
              .map((media) => media.asset_id);
          }
        });
        return merged;
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not load proposed events.';
      showError(message);
    } finally {
      setLoading(false);
    }
  }, [showError, token]);

  React.useEffect(() => {
    void loadProposals();
  }, [loadProposals]);

  React.useEffect(() => {
    let mounted = true;
    void Promise.all([
      apiFetch('/mobile/contacts', { token }),
      apiFetch('/mobile/places?limit=500', { token }),
    ])
      .then(([contactsResponse, placesResponse]) => {
        if (!mounted) return;
        setAvailableContacts(
          ((contactsResponse as { contacts?: EventContactOption[] }).contacts || []).filter(
            (contact) => contact?.contact_id && contact?.display_name,
          ),
        );
        setAvailablePlaces((placesResponse as { places?: EventPlaceOption[] }).places || []);
      })
      .catch(() => {
        if (mounted) {
          setAvailableContacts([]);
          setAvailablePlaces([]);
        }
      });
    return () => {
      mounted = false;
    };
  }, [token]);

  const refresh = React.useCallback(async () => {
    setRefreshing(true);
    try {
      await loadProposals();
    } finally {
      setRefreshing(false);
    }
  }, [loadProposals]);

  const runScan = React.useCallback(async () => {
    setRunningScan(true);
    try {
      const result = (await apiFetch('/mobile/proposed-events/run', {
        method: 'POST',
        token,
        body: JSON.stringify(todayPayload()),
      })) as { created?: number };
      await loadProposals();
      showSuccess(`Scan complete. ${result.created ?? 0} new proposals.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not run the scan.';
      showError(message);
    } finally {
      setRunningScan(false);
    }
  }, [loadProposals, showError, showSuccess, token]);

  const updateProposal = React.useCallback(
    async (proposal: ProposedEvent, action: 'accept' | 'dismiss' | 'ignore') => {
      setSavingId(proposal.proposal_id);
      try {
        const body =
          action === 'accept'
            ? JSON.stringify({
                title:
                  draftTitleById[proposal.proposal_id] ||
                  proposal.suggested_title ||
                  'Untitled event',
                summary: draftSummaryById[proposal.proposal_id] ?? proposal.suggested_summary ?? '',
                startAt: draftStartById[proposal.proposal_id] || proposal.start_at,
                endAt: draftEndById[proposal.proposal_id] || proposal.end_at,
                contactIds: draftContactIdsById[proposal.proposal_id] || [],
                placeId: draftPlaceIdById[proposal.proposal_id] || '',
                placeName: draftPlaceTextById[proposal.proposal_id] || '',
                ...(selectedPlaceById[proposal.proposal_id]
                  ? { placeCandidateId: selectedPlaceById[proposal.proposal_id] }
                  : {}),
                mediaAssetIds:
                  selectedMediaIdsById[proposal.proposal_id] ??
                  (proposal.media_suggestions || [])
                    .filter((media) => media.status !== 'removed')
                    .map((media) => media.asset_id),
              })
            : undefined;
        const response = (await apiFetch(
          `/mobile/proposed-events/${encodeURIComponent(proposal.proposal_id)}/${action}`,
          {
            method: 'POST',
            token,
            ...(body ? { body } : {}),
          },
        )) as { proposal?: ProposedEvent };
        setProposals((current) =>
          current.filter((item) => item.proposal_id !== proposal.proposal_id),
        );
        if (action === 'accept') {
          const eventId = response.proposal?.accepted_event_id || response.proposal?.event_id;
          showSuccess('Event created.');
          if (eventId) {
            router.push(`/events/${encodeURIComponent(eventId)}`);
          }
        } else {
          showSuccess(action === 'ignore' ? 'Place ignored.' : 'Proposal dismissed.');
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : `Could not ${action} proposal.`;
        showError(message);
      } finally {
        setSavingId(null);
      }
    },
    [
      draftContactIdsById,
      draftEndById,
      draftPlaceIdById,
      draftPlaceTextById,
      draftStartById,
      draftSummaryById,
      draftTitleById,
      router,
      selectedMediaIdsById,
      selectedPlaceById,
      showError,
      showSuccess,
      token,
    ],
  );

  const removeSuggestedMedia = React.useCallback(
    async (proposal: ProposedEvent, assetId: string) => {
      const currentIds = selectedMediaIdsById[proposal.proposal_id] ?? [];
      const nextIds = currentIds.filter((id) => id !== assetId);
      setSelectedMediaIdsById((current) => ({
        ...current,
        [proposal.proposal_id]: nextIds,
      }));
      try {
        await apiFetch(
          `/mobile/proposed-events/${encodeURIComponent(proposal.proposal_id)}/media-selection`,
          {
            method: 'POST',
            token,
            body: JSON.stringify({ mediaAssetIds: nextIds }),
          },
        );
      } catch (error) {
        setSelectedMediaIdsById((current) => ({
          ...current,
          [proposal.proposal_id]: currentIds,
        }));
        showError(error instanceof Error ? error.message : 'Could not remove suggested media.');
      }
    },
    [selectedMediaIdsById, showError, token],
  );

  const pendingCount = proposals.filter((proposal) => proposal.status === 'pending').length;

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={0}
      >
        <Animated.ScrollView
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
            useNativeDriver: false,
          })}
          scrollEventThrottle={16}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
          automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
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
          <Card style={styles.summaryCard}>
            <View style={styles.summaryIcon}>
              <Ionicons name="calendar-outline" size={20} color={theme.colors.teal} />
            </View>
            <View style={styles.summaryText}>
              <Text style={styles.summaryTitle}>
                {pendingCount === 1 ? '1 possible event' : `${pendingCount} possible events`}
              </Text>
              <Text style={styles.summarySubtitle}>
                Daily scans review today and yesterday, and keep suggestions for 7 days.
              </Text>
            </View>
          </Card>

          <Button
            label={runningScan ? 'Scanning...' : 'Scan today'}
            onPress={() => {
              void runScan();
            }}
            loading={runningScan}
            variant="secondary"
            style={styles.scanButton}
          />

          {!loading && proposals.length === 0 ? (
            <Card style={styles.emptyCard}>
              <Text style={styles.emptyTitle}>Nothing to review</Text>
              <Text style={styles.emptyText}>
                When your location history shows a meaningful stay without a matching event, it will
                appear here.
              </Text>
            </Card>
          ) : null}

          {proposals.map((proposal) => {
            const isActive = activeId === proposal.proposal_id;
            const isSaving = savingId === proposal.proposal_id;
            return (
              <Card key={proposal.proposal_id} style={styles.proposalCard}>
                <Pressable
                  onPress={() => setActiveId(isActive ? null : proposal.proposal_id)}
                  style={styles.proposalHeader}
                >
                  <View style={styles.proposalIcon}>
                    <Ionicons name="location-outline" size={18} color={theme.colors.accentDeep} />
                  </View>
                  <View style={styles.proposalTitleBlock}>
                    <Text style={styles.proposalTitle}>
                      {proposal.suggested_title || placeLabel(proposal)}
                    </Text>
                    <Text style={styles.proposalMeta}>
                      {formatTimeRange(proposal.start_at, proposal.end_at)}
                    </Text>
                    <Text style={styles.proposalMeta}>
                      {proposal.duration_label || `${proposal.duration_minutes} min`} ·{' '}
                      {proposal.confidence} confidence
                    </Text>
                  </View>
                  <Ionicons
                    name={isActive ? 'chevron-up' : 'chevron-down'}
                    size={20}
                    color={theme.colors.mutedInk}
                  />
                </Pressable>

                {isActive ? (
                  <View style={styles.editor}>
                    <Text style={styles.fieldLabel}>When</Text>
                    <Text style={styles.helperText}>Start</Text>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel="Edit event start date and time"
                      onPress={() =>
                        setActivePicker({ proposalId: proposal.proposal_id, field: 'start' })
                      }
                      style={styles.dateField}
                    >
                      <Text style={styles.dateValue}>
                        {draftStartById[proposal.proposal_id]
                          ? formatDraftDateTime(draftStartById[proposal.proposal_id])
                          : 'Add start date and time'}
                      </Text>
                    </Pressable>
                    <Text style={styles.helperText}>End</Text>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel="Edit event end date and time"
                      onPress={() =>
                        setActivePicker({ proposalId: proposal.proposal_id, field: 'end' })
                      }
                      style={styles.dateField}
                    >
                      <Text style={styles.dateValue}>
                        {draftEndById[proposal.proposal_id]
                          ? formatDraftDateTime(draftEndById[proposal.proposal_id])
                          : 'Add end date and time'}
                      </Text>
                    </Pressable>

                    <Text style={styles.fieldLabel}>Place</Text>
                    <TextInput
                      value={draftPlaceTextById[proposal.proposal_id] ?? ''}
                      onChangeText={(value) => {
                        setDraftPlaceTextById((current) => ({
                          ...current,
                          [proposal.proposal_id]: value,
                        }));
                        setDraftPlaceIdById((current) => ({
                          ...current,
                          [proposal.proposal_id]: null,
                        }));
                        setSelectedPlaceById((current) => ({
                          ...current,
                          [proposal.proposal_id]: null,
                        }));
                      }}
                      style={styles.input}
                      placeholder="Search places"
                      placeholderTextColor={theme.colors.mutedInk}
                    />
                    {draftPlaceTextById[proposal.proposal_id]?.trim() && availablePlaces.length ? (
                      <View style={styles.suggestionList}>
                        {availablePlaces
                          .filter((place) =>
                            normalizeSearch(placeOptionLabel(place)).includes(
                              normalizeSearch(draftPlaceTextById[proposal.proposal_id] || ''),
                            ),
                          )
                          .slice(0, 5)
                          .map((place) => (
                            <Pressable
                              key={place.place_id}
                              accessibilityRole="button"
                              accessibilityLabel={`Select ${placeOptionLabel(place)}`}
                              onPress={() => {
                                setDraftPlaceTextById((current) => ({
                                  ...current,
                                  [proposal.proposal_id]: placeOptionLabel(place),
                                }));
                                setDraftPlaceIdById((current) => ({
                                  ...current,
                                  [proposal.proposal_id]: place.place_id,
                                }));
                                setSelectedPlaceById((current) => ({
                                  ...current,
                                  [proposal.proposal_id]: null,
                                }));
                              }}
                              style={styles.suggestionRow}
                            >
                              <Text style={styles.suggestionText}>{placeOptionLabel(place)}</Text>
                              <Ionicons
                                name="location-outline"
                                size={16}
                                color={theme.colors.accentDeep}
                              />
                            </Pressable>
                          ))}
                      </View>
                    ) : null}
                    {proposal.place_candidates?.length ? (
                      <View>
                        <Text style={styles.fieldLabel}>Choose the place</Text>
                        <Text style={styles.placeHint}>
                          Select the venue that matches your visit. The selected place will be saved
                          for future location matching.
                        </Text>
                        <View style={styles.placeCandidates}>
                          {proposal.place_candidates.slice(0, 3).map((candidate) => {
                            const selected =
                              selectedPlaceById[proposal.proposal_id] ===
                              candidate.provider_place_id;
                            const candidateType = candidate.primary_type?.replaceAll('_', ' ');
                            return (
                              <Pressable
                                key={candidate.provider_place_id}
                                onPress={() => {
                                  setSelectedPlaceById((current) => ({
                                    ...current,
                                    [proposal.proposal_id]: candidate.provider_place_id,
                                  }));
                                  setDraftPlaceIdById((current) => ({
                                    ...current,
                                    [proposal.proposal_id]: null,
                                  }));
                                  setDraftPlaceTextById((current) => ({
                                    ...current,
                                    [proposal.proposal_id]: candidate.title || '',
                                  }));
                                }}
                                style={[
                                  styles.placeCandidate,
                                  selected && styles.placeCandidateSelected,
                                ]}
                              >
                                <View style={styles.placeCandidateCopy}>
                                  <Text style={styles.placeCandidateTitle}>
                                    {candidate.title || 'Unnamed place'}
                                  </Text>
                                  <Text style={styles.placeCandidateMeta}>
                                    {[
                                      candidateType,
                                      candidate.distance_m != null
                                        ? `${Math.round(candidate.distance_m)}m away`
                                        : null,
                                    ]
                                      .filter(Boolean)
                                      .join(' · ')}
                                  </Text>
                                  {candidate.formatted_address ? (
                                    <Text style={styles.placeCandidateAddress}>
                                      {candidate.formatted_address}
                                    </Text>
                                  ) : null}
                                </View>
                                <Ionicons
                                  name={selected ? 'checkmark-circle' : 'ellipse-outline'}
                                  size={22}
                                  color={selected ? theme.colors.teal : theme.colors.mutedInk}
                                />
                              </Pressable>
                            );
                          })}
                        </View>
                      </View>
                    ) : null}

                    <Text style={styles.fieldLabel}>People</Text>
                    <TextInput
                      value={participantQueryById[proposal.proposal_id] || ''}
                      onChangeText={(value) =>
                        setParticipantQueryById((current) => ({
                          ...current,
                          [proposal.proposal_id]: value,
                        }))
                      }
                      style={styles.input}
                      placeholder="Search contacts"
                      placeholderTextColor={theme.colors.mutedInk}
                    />
                    {(draftContactIdsById[proposal.proposal_id] || []).length ? (
                      <View style={styles.chipRow}>
                        {(draftContactIdsById[proposal.proposal_id] || []).map((contactId) => {
                          const contact = availableContacts.find(
                            (item) => item.contact_id === contactId,
                          );
                          return (
                            <Pressable
                              key={contactId}
                              onPress={() =>
                                setDraftContactIdsById((current) => ({
                                  ...current,
                                  [proposal.proposal_id]: (
                                    current[proposal.proposal_id] || []
                                  ).filter((id) => id !== contactId),
                                }))
                              }
                              style={styles.chip}
                            >
                              <Text style={styles.chipText}>
                                {contact?.display_name || contactId}
                              </Text>
                              <Ionicons name="close" size={12} color={theme.colors.mutedInk} />
                            </Pressable>
                          );
                        })}
                      </View>
                    ) : (
                      <Text style={styles.helperText}>No people selected.</Text>
                    )}
                    {participantQueryById[proposal.proposal_id]?.trim() ? (
                      <View style={styles.suggestionList}>
                        {availableContacts
                          .filter(
                            (contact) =>
                              !(draftContactIdsById[proposal.proposal_id] || []).includes(
                                contact.contact_id,
                              ) &&
                              matchesContactSearch(
                                contact,
                                participantQueryById[proposal.proposal_id] || '',
                              ),
                          )
                          .slice(0, 5)
                          .map((contact) => (
                            <Pressable
                              key={contact.contact_id}
                              onPress={() => {
                                setDraftContactIdsById((current) => ({
                                  ...current,
                                  [proposal.proposal_id]: [
                                    ...(current[proposal.proposal_id] || []),
                                    contact.contact_id,
                                  ],
                                }));
                                setParticipantQueryById((current) => ({
                                  ...current,
                                  [proposal.proposal_id]: '',
                                }));
                              }}
                              style={styles.suggestionRow}
                            >
                              <Text style={styles.suggestionText}>{contact.display_name}</Text>
                              <Ionicons name="add" size={16} color={theme.colors.accentDeep} />
                            </Pressable>
                          ))}
                      </View>
                    ) : null}
                    <Text style={styles.fieldLabel}>Title</Text>
                    <TextInput
                      value={draftTitleById[proposal.proposal_id] ?? proposal.suggested_title ?? ''}
                      onChangeText={(value) =>
                        setDraftTitleById((current) => ({
                          ...current,
                          [proposal.proposal_id]: value,
                        }))
                      }
                      style={styles.input}
                      placeholder="Event title"
                      placeholderTextColor={theme.colors.mutedInk}
                    />
                    <Text style={styles.fieldLabel}>Summary</Text>
                    <TextInput
                      value={
                        draftSummaryById[proposal.proposal_id] ?? proposal.suggested_summary ?? ''
                      }
                      onChangeText={(value) =>
                        setDraftSummaryById((current) => ({
                          ...current,
                          [proposal.proposal_id]: value,
                        }))
                      }
                      style={[styles.input, styles.summaryInput]}
                      multiline
                      placeholder="What happened?"
                      placeholderTextColor={theme.colors.mutedInk}
                    />
                    {proposal.reason ? (
                      <>
                        <Text style={styles.fieldLabel}>Why suggested</Text>
                        <Text style={styles.reasonText}>{proposal.reason}</Text>
                      </>
                    ) : null}
                    <EventMediaSuggestionCard
                      suggestions={(proposal.media_suggestions || []).filter((media) =>
                        (selectedMediaIdsById[proposal.proposal_id] || []).includes(media.asset_id),
                      )}
                      token={token}
                      onRemove={(assetId) => {
                        void removeSuggestedMedia(proposal, assetId);
                      }}
                    />
                    <View style={styles.actions}>
                      <Button
                        label="Create event"
                        onPress={() => {
                          void updateProposal(proposal, 'accept');
                        }}
                        loading={isSaving}
                        style={styles.primaryAction}
                      />
                      <View style={styles.secondaryActions}>
                        <Button
                          label="Dismiss"
                          onPress={() => {
                            void updateProposal(proposal, 'dismiss');
                          }}
                          disabled={isSaving}
                          variant="secondary"
                          style={styles.secondaryAction}
                        />
                        <Button
                          label="Ignore place"
                          onPress={() => {
                            void updateProposal(proposal, 'ignore');
                          }}
                          disabled={isSaving}
                          variant="danger"
                          style={styles.secondaryAction}
                        />
                      </View>
                    </View>
                  </View>
                ) : null}
              </Card>
            );
          })}
        </Animated.ScrollView>

        {activePicker ? (
          <UiDirectiveDateTimePickerSheet
            visible
            mode="datetime"
            value={(() => {
              const draftValue =
                activePicker.field === 'start'
                  ? draftStartById[activePicker.proposalId]
                  : draftEndById[activePicker.proposalId];
              return draftValue ? draftDateTimePickerValue(draftValue) : undefined;
            })()}
            onClose={() => setActivePicker(null)}
            onConfirm={(value) => {
              if (activePicker.field === 'start') {
                setDraftStartById((current) => ({
                  ...current,
                  [activePicker.proposalId]: value,
                }));
              } else {
                setDraftEndById((current) => ({
                  ...current,
                  [activePicker.proposalId]: value,
                }));
              }
              setActivePicker(null);
            }}
          />
        ) : null}

        <CollapsingTopBar
          title="Proposed events"
          secondaryTitle="Fill in your day"
          scrollY={scrollY}
          onPressBack={() => router.back()}
        />
      </KeyboardAvoidingView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  screen: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 24,
  },
  summaryCard: {
    borderRadius: theme.radius.xl,
    padding: 18,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
  },
  summaryIcon: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  summaryText: {
    flex: 1,
  },
  summaryTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  summarySubtitle: {
    marginTop: 4,
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 18,
  },
  scanButton: {
    marginTop: 14,
  },
  emptyCard: {
    marginTop: 16,
    padding: 20,
    borderRadius: theme.radius.xl,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  emptyText: {
    marginTop: 8,
    fontSize: 14,
    color: theme.colors.mutedInk,
    lineHeight: 20,
  },
  proposalCard: {
    marginTop: 16,
    padding: 0,
    borderRadius: theme.radius.xl,
    overflow: 'hidden',
  },
  proposalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 18,
  },
  proposalIcon: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fdf4f3',
  },
  proposalTitleBlock: {
    flex: 1,
  },
  proposalTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  proposalMeta: {
    marginTop: 4,
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  editor: {
    padding: 18,
    paddingTop: 0,
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
  },
  helperText: {
    marginBottom: 6,
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  dateField: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  dateValue: {
    fontSize: 15,
    color: theme.colors.ink,
  },
  fieldLabel: {
    marginTop: 14,
    marginBottom: 6,
    fontSize: 12,
    fontWeight: '700',
    color: theme.colors.mutedInk,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  placeText: {
    fontSize: 14,
    color: theme.colors.ink,
    fontWeight: '600',
  },
  placeHint: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 18,
  },
  placeCandidates: {
    marginTop: 8,
    gap: 8,
  },
  placeCandidate: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 12,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
  },
  placeCandidateSelected: {
    borderColor: theme.colors.teal,
    backgroundColor: theme.colors.paleTeal,
  },
  placeCandidateCopy: {
    flex: 1,
  },
  placeCandidateTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  placeCandidateMeta: {
    marginTop: 3,
    fontSize: 12,
    color: theme.colors.mutedInk,
    textTransform: 'capitalize',
  },
  placeCandidateAddress: {
    marginTop: 3,
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  suggestionList: {
    marginTop: 8,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    overflow: 'hidden',
  },
  suggestionRow: {
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  suggestionText: {
    flex: 1,
    fontSize: 14,
    color: theme.colors.ink,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 10,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 16,
    backgroundColor: theme.colors.paleTeal,
  },
  chipText: {
    fontSize: 13,
    color: theme.colors.ink,
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: theme.colors.ink,
  },
  summaryInput: {
    minHeight: 92,
    textAlignVertical: 'top',
  },
  reasonText: {
    marginTop: 10,
    fontSize: 13,
    color: theme.colors.mutedInk,
    lineHeight: 18,
  },
  actions: {
    marginTop: 16,
    gap: 10,
  },
  primaryAction: {
    alignSelf: 'stretch',
  },
  secondaryActions: {
    flexDirection: 'row',
    gap: 10,
  },
  secondaryAction: {
    flex: 1,
  },
});
