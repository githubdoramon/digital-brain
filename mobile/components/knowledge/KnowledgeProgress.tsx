import React from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { Text } from 'react-native';
import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { KnowledgeProgressStatus, LearningProgressDetails } from './KnowledgeProgressStatus';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type Progress = {
  pending?: number;
  running?: number;
  failed?: number;
  deferred?: number;
  details?: LearningProgressDetails;
};
export function KnowledgeProgress() {
  const { token } = useAuth();
  const { showError } = useAppNotice();
  const [progress, setProgress] = React.useState<Progress | null>(null);
  const [refreshFailed, setRefreshFailed] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  useFocusEffect(
    React.useCallback(() => {
      let active = true;
      let loading = false;
      setProgress(null);
      const load = () => {
        if (loading) return;
        loading = true;
        void (apiFetch('/mobile/knowledge/progress', { token }) as Promise<Progress>)
          .then((result) => {
            if (active) {
              setProgress(result);
              setRefreshFailed(false);
            }
          })
          .catch(() => {
            if (active) setRefreshFailed(true);
          })
          .finally(() => {
            loading = false;
          });
      };
      load();
      const timer = setInterval(load, 15000);
      return () => {
        active = false;
        clearInterval(timer);
      };
    }, [token]),
  );
  const retry = async () => {
    setBusy(true);
    try {
      await apiFetch('/mobile/knowledge/retry', { token, method: 'POST' });
      setProgress((await apiFetch('/mobile/knowledge/progress', { token })) as Progress);
      setRefreshFailed(false);
    } catch {
      showError('Could not resume learning.');
    } finally {
      setBusy(false);
    }
  };
  if (!progress && !refreshFailed) return null;
  const details = progress?.details;
  if (
    !details &&
    !refreshFailed &&
    !progress?.pending &&
    !progress?.running &&
    !progress?.failed &&
    !progress?.deferred
  )
    return null;
  const needsRetry =
    progress?.failed ||
    (details &&
      [
        details.learning.history,
        details.learning.ongoing,
        details.intelligence.history,
        details.intelligence.scheduled,
        details.summaries,
      ].some((lane) => lane.paused || lane.review_paused || lane.review_retrying || lane.retrying));
  return (
    <Card style={{ padding: 18, gap: 10 }}>
      {refreshFailed ? (
        <Text style={{ color: theme.colors.mutedInk }}>Could not refresh learning progress.</Text>
      ) : null}
      {details ? <KnowledgeProgressStatus details={details} /> : null}
      {!details && (progress?.pending || progress?.running) ? (
        <Text style={{ color: theme.colors.ink }}>Your Brain is learning.</Text>
      ) : null}
      {!details && progress?.deferred ? (
        <Text style={{ color: theme.colors.mutedInk }}>
          Some information is waiting for clearer context.
        </Text>
      ) : null}
      {needsRetry ? (
        <>
          <Text style={{ color: theme.colors.ink }}>
            Some information could not be reviewed yet.
          </Text>
          <Button
            label="Try learning again"
            variant="secondary"
            disabled={busy}
            onPress={() => void retry()}
          />
        </>
      ) : null}
    </Card>
  );
}
