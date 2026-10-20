import React from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { Text } from 'react-native';
import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type Progress = { pending?: number; running?: number; failed?: number; deferred?: number };
export function KnowledgeProgress() {
  const { token } = useAuth();
  const { showError } = useAppNotice();
  const [progress, setProgress] = React.useState<Progress>({});
  const [busy, setBusy] = React.useState(false);
  useFocusEffect(
    React.useCallback(() => {
      let active = true;
      const load = () =>
        void (apiFetch('/mobile/knowledge/progress', { token }) as Promise<Progress>)
          .then((result) => {
            if (active) setProgress(result);
          })
          .catch(() => undefined);
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
    } catch {
      showError('Could not resume learning.');
    } finally {
      setBusy(false);
    }
  };
  if (!progress.pending && !progress.running && !progress.failed && !progress.deferred) return null;
  return (
    <Card style={{ padding: 18, gap: 10 }}>
      {progress.pending || progress.running ? (
        <Text style={{ color: theme.colors.ink }}>Your Brain is learning from your history.</Text>
      ) : null}
      {progress.deferred ? (
        <Text style={{ color: theme.colors.mutedInk }}>
          Some information is waiting for clearer context.
        </Text>
      ) : null}
      {progress.failed ? (
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
