import React from 'react';
import { Text, View } from 'react-native';
import { theme } from '@/theme';

type Lane = {
  retired?: boolean;
  total: number;
  processed: number;
  remaining: number;
  ready: number;
  running: number;
  retrying: number;
  paused: number;
  deferred: number;
  not_enqueued: number;
  delayed: number;
  review_retrying?: number;
  review_paused?: number;
  running_runs?: number;
  last_success_at: string | null;
  oldest_pending_source_at: string | null;
  state: 'initializing' | 'processing' | 'pending' | 'waiting' | 'caught_up';
};

export type LearningProgressDetails = {
  as_of: string;
  initialized: boolean;
  history_caught_up: boolean;
  learning: { ongoing: Lane; history: Lane };
  intelligence: { history: Lane; scheduled: Lane };
  summaries: Lane;
  indexing: { remaining: number };
};

function displayDate(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function ProgressLane({
  title,
  lane,
  summaries = false,
}: {
  title: string;
  lane: Lane;
  summaries?: boolean;
}) {
  const waiting = lane.ready + lane.not_enqueued + lane.delayed;
  const retrying = lane.retrying + (lane.review_retrying ?? 0);
  const paused = lane.paused + (lane.review_paused ?? 0);
  const notes = [
    lane.running ? `${lane.running.toLocaleString()} in progress` : null,
    lane.running_runs ? 'Reviewing now' : null,
    waiting ? `${waiting.toLocaleString()} waiting` : null,
    retrying ? `${retrying.toLocaleString()} waiting to retry` : null,
    paused ? `${paused.toLocaleString()} need another try` : null,
    lane.deferred ? `${lane.deferred.toLocaleString()} waiting for clearer context` : null,
  ].filter(Boolean);
  return (
    <View style={{ gap: 3 }}>
      <Text style={{ color: theme.colors.ink, fontWeight: '600' }}>{title}</Text>
      <Text style={{ color: theme.colors.mutedInk }}>
        {lane.processed.toLocaleString()} of {lane.total.toLocaleString()}{' '}
        {summaries ? 'summaries up to date' : 'passages reviewed'}
        {lane.remaining ? ` · ${lane.remaining.toLocaleString()} remaining` : ''}
      </Text>
      {notes.length ? (
        <Text style={{ color: theme.colors.mutedInk }}>{notes.join(' · ')}</Text>
      ) : null}
      {lane.remaining && lane.oldest_pending_source_at ? (
        <Text style={{ color: theme.colors.mutedInk }}>
          Oldest waiting information: {new Date(lane.oldest_pending_source_at).toLocaleDateString()}
        </Text>
      ) : null}
      {lane.last_success_at ? (
        <Text style={{ color: theme.colors.mutedInk }}>
          Last {summaries ? 'updated' : 'reviewed'}: {displayDate(lane.last_success_at)}
        </Text>
      ) : null}
    </View>
  );
}

export function KnowledgeProgressStatus({ details }: { details: LearningProgressDetails }) {
  if (!details.initialized) {
    return (
      <Text style={{ color: theme.colors.ink }}>Preparing your saved history for learning.</Text>
    );
  }
  return (
    <View style={{ gap: 14 }}>
      <Text style={{ color: theme.colors.ink, fontWeight: '600' }}>
        {details.history_caught_up
          ? 'Your saved history is up to date.'
          : 'Learning from your saved history'}
      </Text>
      <ProgressLane title="History learning" lane={details.learning.history} />
      {!details.intelligence.history.retired ? (
        <ProgressLane title="Older messages and files" lane={details.intelligence.history} />
      ) : null}
      {details.learning.ongoing.total ? (
        <ProgressLane title="Ongoing learning" lane={details.learning.ongoing} />
      ) : null}
      {details.intelligence.scheduled.total ? (
        <ProgressLane title="New messages and files" lane={details.intelligence.scheduled} />
      ) : null}
      {details.summaries.total ? (
        <ProgressLane title="Summaries" lane={details.summaries} summaries />
      ) : null}
      {details.indexing.remaining ? (
        <Text style={{ color: theme.colors.mutedInk }}>
          Improving search for {details.indexing.remaining.toLocaleString()} learned facts.
        </Text>
      ) : null}
      <Text style={{ color: theme.colors.mutedInk, fontSize: 12 }}>
        Status updated {displayDate(details.as_of)}
      </Text>
    </View>
  );
}
