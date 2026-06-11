import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
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
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type ProposedEvent = {
  proposal_id: string;
  status: 'pending' | 'accepted' | 'dismissed' | 'ignored' | 'expired';
  start_at: string;
  end_at: string;
  duration_minutes: number;
  place_id?: string | null;
  place_name?: string | null;
  city?: string | null;
  country?: string | null;
  confidence: 'medium' | 'high';
  reason?: string | null;
  suggested_title?: string | null;
  suggested_summary?: string | null;
  suggested_contact_ids?: string[];
  accepted_event_id?: string | null;
  event_id?: string | null;
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
  const targetDate = now.toISOString().slice(0, 10);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  return { targetDate, timezone };
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
                title: draftTitleById[proposal.proposal_id] || proposal.suggested_title || 'Untitled event',
                summary: draftSummaryById[proposal.proposal_id] || proposal.suggested_summary || '',
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
    [draftSummaryById, draftTitleById, router, showError, showSuccess, token],
  );

  const pendingCount = proposals.filter((proposal) => proposal.status === 'pending').length;

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <Animated.ScrollView
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
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
              Daily scans run after 20:30 and keep suggestions for 7 days.
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
              When your location history shows a meaningful stay without a matching event, it will appear here.
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
                  <Text style={styles.proposalTitle}>{proposal.suggested_title || placeLabel(proposal)}</Text>
                  <Text style={styles.proposalMeta}>{formatTimeRange(proposal.start_at, proposal.end_at)}</Text>
                  <Text style={styles.proposalMeta}>
                    {proposal.duration_minutes} min · {proposal.confidence} confidence
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
                  <Text style={styles.fieldLabel}>Place</Text>
                  <Text style={styles.placeText}>{placeLabel(proposal)}</Text>
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
                  <Text style={styles.fieldLabel}>Notes</Text>
                  <TextInput
                    value={draftSummaryById[proposal.proposal_id] ?? proposal.suggested_summary ?? ''}
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
                  {proposal.suggested_contact_ids?.length ? (
                    <Text style={styles.reasonText}>
                      Suggested people: {proposal.suggested_contact_ids.join(', ')}
                    </Text>
                  ) : null}
                  <Text style={styles.reasonText}>{proposal.reason}</Text>
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

      <CollapsingTopBar
        title="Proposed events"
        secondaryTitle="Fill in your day"
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
