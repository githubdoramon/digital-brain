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
import { ArchiveEvidence, type ArchiveSource } from '@/components/ArchiveEvidence';
import { ContactPicker } from '@/components/ContactPicker';
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
import { normalizeSearch } from '@/utils/text';
import { formatBirthday } from '@/utils/birthdays';
import { fetchContactGroups, type ContactGroup } from '@/utils/contactGroups';
import { proposalAction, type ContactChangeGraph } from '@/utils/proposalActions';

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
  selected_place_candidate_id?: string | null;
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

type ProposedEnrichment = {
  proposal_id: string;
  kind: 'profession' | 'relationship' | 'task' | 'project' | 'task_change' | 'contact_change';
  contact_id: string;
  contact_name: string;
  related_contact_id?: string | null;
  related_contact_name?: string | null;
  suggested_value: string;
  reciprocal_value?: string | null;
  source_event_id?: string | null;
  source_event_title?: string | null;
  confidence: 'low' | 'medium' | 'high';
  evidence?: string | null;
  payload?: {
    due_date?: string | null;
    assignee_kind?: string;
    assignee_name?: string | null;
    requested_status?: string;
    target_status?: string;
    contact_changes?: ContactChangeGraph;
    contact_plan?: {
      choices: {
        ref: string;
        display_name: string;
        can_create: boolean;
        candidates: { contact_id: string; display_name: string }[];
      }[];
      review_reasons: string[];
    };
  };
  source_evidence?: ArchiveSource[];
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
  const [enrichmentProposals, setEnrichmentProposals] = React.useState<ProposedEnrichment[]>([]);
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
  const [contactGroups, setContactGroups] = React.useState<ContactGroup[]>([]);
  const [availablePlaces, setAvailablePlaces] = React.useState<EventPlaceOption[]>([]);
  const [activePicker, setActivePicker] = React.useState<{
    proposalId: string;
    field: 'start' | 'end';
  } | null>(null);
  const [savingId, setSavingId] = React.useState<string | null>(null);
  const [contactResolutions, setContactResolutions] = React.useState<
    Record<string, Record<string, string>>
  >({});

  const loadProposals = React.useCallback(async () => {
    setLoading(true);
    try {
      const [eventResponse, enrichmentResponse] = (await Promise.all([
        apiFetch('/mobile/proposed-events', { token }),
        apiFetch('/mobile/proposals', { token }),
      ])) as [{ proposals?: ProposedEvent[] }, { proposals?: ProposedEnrichment[] }];
      const next = Array.isArray(eventResponse?.proposals) ? eventResponse.proposals : [];
      setEnrichmentProposals(
        Array.isArray(enrichmentResponse?.proposals) ? enrichmentResponse.proposals : [],
      );
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
            const selectedCandidate = proposal.place_candidates?.find(
              (candidate) => candidate.provider_place_id === proposal.selected_place_candidate_id,
            );
            merged[proposal.proposal_id] = selectedCandidate?.title || proposal.place_name || '';
          }
        });
        return merged;
      });
      setSelectedPlaceById((current) => {
        const merged = { ...current };
        next.forEach((proposal) => {
          if (!Object.prototype.hasOwnProperty.call(merged, proposal.proposal_id)) {
            const selectedCandidateId = proposal.selected_place_candidate_id;
            merged[proposal.proposal_id] =
              selectedCandidateId &&
              proposal.place_candidates?.some(
                (candidate) => candidate.provider_place_id === selectedCandidateId,
              )
                ? selectedCandidateId
                : null;
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
      const message = error instanceof Error ? error.message : 'Could not load proposed items.';
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
      fetchContactGroups().catch(() => []),
    ])
      .then(([contactsResponse, placesResponse, groupResponse]) => {
        if (!mounted) return;
        setAvailableContacts(
          ((contactsResponse as { contacts?: EventContactOption[] }).contacts || []).filter(
            (contact) => contact?.contact_id && contact?.display_name,
          ),
        );
        setAvailablePlaces((placesResponse as { places?: EventPlaceOption[] }).places || []);
        setContactGroups(groupResponse);
      })
      .catch(() => {
        if (mounted) {
          setAvailableContacts([]);
          setAvailablePlaces([]);
          setContactGroups([]);
        }
      });
    return () => {
      mounted = false;
    };
  }, [token]);

  const addProposalContacts = React.useCallback((proposalId: string, contactIds: string[]) => {
    setDraftContactIdsById((current) => ({
      ...current,
      [proposalId]: Array.from(new Set([...(current[proposalId] || []), ...contactIds])),
    }));
    setParticipantQueryById((current) => ({ ...current, [proposalId]: '' }));
  }, []);

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

  const updateEnrichmentProposal = React.useCallback(
    async (proposal: ProposedEnrichment, action: 'accept' | 'dismiss') => {
      if (action === 'accept' && !proposalAction(proposal).supported) {
        showError('This suggestion needs review before it can be applied.');
        return;
      }
      setSavingId(proposal.proposal_id);
      try {
        await apiFetch(`/mobile/proposals/${encodeURIComponent(proposal.proposal_id)}/${action}`, {
          method: 'POST',
          token,
          ...(action === 'accept' && proposal.kind === 'contact_change'
            ? {
                body: JSON.stringify({
                  contact_resolutions: contactResolutions[proposal.proposal_id] || {},
                }),
              }
            : {}),
        });
        setEnrichmentProposals((current) =>
          current.filter((item) => item.proposal_id !== proposal.proposal_id),
        );
        showSuccess(action === 'accept' ? 'Changes applied.' : 'Suggestion dismissed.');
      } catch (error) {
        showError(error instanceof Error ? error.message : 'Could not update suggestion.');
      } finally {
        setSavingId(null);
      }
    },
    [showError, showSuccess, token, contactResolutions],
  );

  const pendingCount =
    proposals.filter((proposal) => proposal.status === 'pending').length +
    enrichmentProposals.length;

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
                {pendingCount === 1 ? '1 item to review' : `${pendingCount} items to review`}
              </Text>
              <Text style={styles.summarySubtitle}>
                Review possible events and contact details suggested from your memories.
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

          {!loading && proposals.length === 0 && enrichmentProposals.length === 0 ? (
            <Card style={styles.emptyCard}>
              <Text style={styles.emptyTitle}>Nothing to review</Text>
              <Text style={styles.emptyText}>
                Possible events and contact suggestions will appear here when they are ready.
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
                    <ContactPicker
                      query={participantQueryById[proposal.proposal_id] || ''}
                      onQueryChange={(value) =>
                        setParticipantQueryById((current) => ({
                          ...current,
                          [proposal.proposal_id]: value,
                        }))
                      }
                      contacts={availableContacts}
                      groups={contactGroups}
                      selectedContactIds={new Set(draftContactIdsById[proposal.proposal_id] || [])}
                      includeGroups
                      maxResults={5}
                      inputStyle={styles.input}
                      onSelectContact={(contact) =>
                        addProposalContacts(proposal.proposal_id, [contact.contact_id])
                      }
                      onSelectGroup={(group) =>
                        addProposalContacts(
                          proposal.proposal_id,
                          group.members.map((member) => member.contact_id),
                        )
                      }
                    />
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

          {enrichmentProposals.length ? (
            <>
              <Text style={styles.sectionTitle}>Suggestions</Text>
              {enrichmentProposals.map((proposal) => {
                const isSaving = savingId === proposal.proposal_id;
                const isRelationship = proposal.kind === 'relationship';
                const isContactChange = proposal.kind === 'contact_change';
                const isWork = ['task', 'project', 'task_change'].includes(proposal.kind);
                const graph = proposal.payload?.contact_changes;
                const action = proposalAction(proposal, contactResolutions[proposal.proposal_id]);
                const choices = proposal.payload?.contact_plan?.choices || [];
                const needsContactChoice = choices.some(
                  (choice) => !contactResolutions[proposal.proposal_id]?.[choice.ref],
                );
                return (
                  <Card key={proposal.proposal_id} style={styles.enrichmentCard}>
                    <View style={styles.proposalHeader}>
                      <View style={styles.proposalIcon}>
                        <Ionicons
                          name={
                            isRelationship || isContactChange
                              ? 'people-outline'
                              : 'briefcase-outline'
                          }
                          size={18}
                          color={theme.colors.accentDeep}
                        />
                      </View>
                      <View style={styles.proposalTitleBlock}>
                        <Text style={styles.proposalTitle}>
                          {isContactChange
                            ? action.title || 'Contact changes'
                            : isWork
                              ? proposal.suggested_value
                              : isRelationship
                                ? `${proposal.contact_name} · ${proposal.related_contact_name || 'Contact'}`
                                : proposal.kind === 'profession'
                                  ? proposal.contact_name
                                  : 'Suggested change'}
                        </Text>
                        <Text style={styles.proposalMeta}>
                          {isContactChange
                            ? action.label
                            : isWork
                              ? `${proposal.kind === 'project' ? 'Project' : proposal.kind === 'task_change' ? (proposal.payload?.requested_status === 'cancelled' ? 'Task cancellation' : 'Task update') : 'Task'}${proposal.payload?.due_date ? ` · Due ${proposal.payload.due_date}` : ''}`
                              : isRelationship
                                ? `${proposal.suggested_value}${proposal.reciprocal_value ? ` · ${proposal.reciprocal_value}` : ''}`
                                : proposal.kind === 'profession'
                                  ? `Profession: ${proposal.suggested_value}`
                                  : proposal.suggested_value}
                        </Text>
                        {!isWork && !isContactChange ? (
                          <Text style={styles.proposalMeta}>
                            {proposal.source_event_title
                              ? `From ${proposal.source_event_title}`
                              : `${proposal.confidence} confidence`}
                          </Text>
                        ) : isWork && proposal.kind !== 'project' ? (
                          <Text style={styles.proposalMeta}>
                            {proposal.payload?.assignee_kind === 'user'
                              ? 'Assigned to you'
                              : proposal.payload?.assignee_name || 'Assignee not yet confirmed'}
                          </Text>
                        ) : null}
                      </View>
                    </View>
                    {isContactChange && graph ? (
                      <View style={{ gap: 8 }}>
                        {graph.people.map((person) => (
                          <Text key={person.ref} style={styles.reasonText}>
                            {person.ref.startsWith('new:')
                              ? `Create or match ${person.display_name}`
                              : person.display_name}
                            {person.birthday
                              ? ` · Birthday: ${formatBirthday(person.birthday)}`
                              : ''}
                          </Text>
                        ))}
                        {graph.relationships.map((rel, index) => (
                          <Text key={index} style={styles.reasonText}>
                            {graph.people.find((p) => p.ref === rel.from_ref)?.display_name}
                            {' → '}
                            {graph.people.find((p) => p.ref === rel.to_ref)?.display_name}
                            {`: ${rel.relationship_type} / ${rel.reciprocal_type}`}
                          </Text>
                        ))}
                        {choices.map((choice) => (
                          <View key={choice.ref} style={{ gap: 6 }}>
                            <Text style={styles.proposalMeta}>Who is {choice.display_name}?</Text>
                            {[
                              ...choice.candidates.map((c) => ({
                                value: c.contact_id,
                                label: c.display_name,
                              })),
                              ...(choice.can_create
                                ? [
                                    {
                                      value: 'create',
                                      label: `Create a new contact for ${choice.display_name}`,
                                    },
                                  ]
                                : []),
                            ].map((option) => {
                              const selected =
                                contactResolutions[proposal.proposal_id]?.[choice.ref] ===
                                option.value;
                              return (
                                <Pressable
                                  key={option.value}
                                  accessibilityRole="radio"
                                  accessibilityState={{ checked: selected }}
                                  onPress={() =>
                                    setContactResolutions((current) => ({
                                      ...current,
                                      [proposal.proposal_id]: {
                                        ...current[proposal.proposal_id],
                                        [choice.ref]: option.value,
                                      },
                                    }))
                                  }
                                >
                                  <Text style={styles.reasonText}>
                                    {selected ? '●' : '○'} {option.label}
                                  </Text>
                                </Pressable>
                              );
                            })}
                          </View>
                        ))}
                      </View>
                    ) : null}
                    {proposal.evidence ? (
                      <Text style={styles.reasonText}>{proposal.evidence}</Text>
                    ) : null}
                    {proposal.source_evidence?.length ? (
                      <ArchiveEvidence sources={proposal.source_evidence} />
                    ) : null}
                    <View style={styles.actions}>
                      <Button
                        label={action.label}
                        onPress={() => void updateEnrichmentProposal(proposal, 'accept')}
                        loading={isSaving}
                        disabled={!action.supported || (isContactChange && needsContactChoice)}
                        style={styles.primaryAction}
                      />
                      <Button
                        label="Dismiss"
                        onPress={() => void updateEnrichmentProposal(proposal, 'dismiss')}
                        disabled={isSaving}
                        variant="secondary"
                        style={styles.secondaryAction}
                      />
                    </View>
                  </Card>
                );
              })}
            </>
          ) : null}
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
          title="Proposed"
          secondaryTitle="Suggested stuff"
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
  sectionTitle: {
    marginTop: 20,
    marginBottom: 10,
    fontSize: 17,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  enrichmentCard: {
    borderRadius: theme.radius.xl,
    padding: 16,
    gap: 10,
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
