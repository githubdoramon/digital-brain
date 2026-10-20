import React from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { ActivityIndicator, Alert, Text, TextInput, View } from 'react-native';
import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { ArchiveEvidence, type ArchiveSource } from '@/components/ArchiveEvidence';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type Fact = {
  knowledge_id: string;
  statement: string;
  category: string;
  inferred: boolean;
  manual: boolean;
  inference_reason?: string | null;
  status: string;
  evidence: ArchiveSource[];
};

export function KnowledgeFacts({
  subject,
  query = '',
  profile = false,
}: {
  subject?: string;
  query?: string;
  profile?: boolean;
}) {
  const { token } = useAuth();
  const { showError, showSuccess } = useAppNotice();
  const [facts, setFacts] = React.useState<Fact[]>([]);
  const [summary, setSummary] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [hasMore, setHasMore] = React.useState(false);
  const [editing, setEditing] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const generation = React.useRef(0);
  const PAGE_SIZE = 30;
  const liveState = React.useRef({ editing, saving, count: facts.length });
  liveState.current = { editing, saving, count: facts.length };

  const load = React.useCallback(
    async (offset = 0, quiet = false) => {
      const current = ++generation.current;
      if (!quiet) setLoading(true);
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (subject) params.set('subject', subject);
      if (profile) params.set('profile', 'true');
      if (query.trim()) params.set('query', query.trim());
      try {
        const [result, description] = await Promise.all([
          apiFetch(`/mobile/knowledge?${params}`, { token }) as Promise<{ knowledge: Fact[] }>,
          subject
            ? (apiFetch(`/mobile/knowledge/summary?subject=${encodeURIComponent(subject)}`, {
                token,
              }) as Promise<{ summary: string | null }>)
            : Promise.resolve(null),
        ]);
        if (generation.current !== current) return;
        setFacts((previous) => (offset ? [...previous, ...result.knowledge] : result.knowledge));
        setHasMore(result.knowledge.length === PAGE_SIZE);
        setSummary(description?.summary ?? null);
      } catch {
        if (generation.current === current && !quiet) showError('Could not load knowledge.');
      } finally {
        if (generation.current === current) setLoading(false);
      }
    },
    [profile, query, showError, subject, token],
  );

  useFocusEffect(
    React.useCallback(() => {
      setFacts([]);
      setSummary(null);
      setEditing(null);
      void load();
      const timer = setInterval(() => {
        if (
          !liveState.current.editing &&
          !liveState.current.saving &&
          liveState.current.count <= PAGE_SIZE
        )
          void load(0, true);
      }, 10000);
      return () => {
        clearInterval(timer);
        generation.current += 1;
      };
    }, [load]),
  );

  const remove = (fact: Fact) =>
    Alert.alert('Delete fact?', 'Remove this from what your Brain remembers.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          setSaving(true);
          try {
            await apiFetch(`/mobile/knowledge/${encodeURIComponent(fact.knowledge_id)}`, {
              token,
              method: 'DELETE',
            });
            setSummary(null);
            setEditing(null);
            await load();
            showSuccess('Fact deleted.');
          } catch {
            showError('Could not delete this fact.');
          } finally {
            setSaving(false);
          }
        },
      },
    ]);

  const save = async (fact: Fact) => {
    if (draft.trim().length < 3) return;
    setSaving(true);
    try {
      await apiFetch(`/mobile/knowledge/${encodeURIComponent(fact.knowledge_id)}`, {
        token,
        method: 'PATCH',
        body: JSON.stringify({ statement: draft.trim() }),
      });
      setSummary(null);
      setEditing(null);
      await load();
      showSuccess('Correction saved.');
    } catch {
      showError('Could not save this correction.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={{ gap: 14 }}>
      {summary && !query ? (
        <Card style={{ padding: 20, gap: 8 }}>
          <Text style={{ color: theme.colors.teal, fontSize: 13, fontWeight: '700' }}>
            OVERVIEW
          </Text>
          <Text style={{ color: theme.colors.ink, fontSize: 17, lineHeight: 25 }}>{summary}</Text>
        </Card>
      ) : null}
      {loading ? <ActivityIndicator color={theme.colors.teal} /> : null}
      {!loading && facts.length === 0 ? (
        <Card style={{ padding: 22 }}>
          <Text style={{ color: theme.colors.ink, fontSize: 17 }}>No facts here yet</Text>
          <Text style={{ color: theme.colors.mutedInk, marginTop: 8 }}>
            {query ? 'Try a different search.' : 'Things your Brain learns will appear here.'}
          </Text>
        </Card>
      ) : null}
      {facts.map((fact) => (
        <Card key={fact.knowledge_id} style={{ padding: 20, gap: 12 }}>
          <Text style={{ color: theme.colors.teal, fontSize: 12, fontWeight: '600' }}>
            {fact.manual ? 'Edited by you' : fact.inferred ? 'Inferred' : 'Learned'} ·{' '}
            {fact.category.replace(/_/g, ' ')}
            {fact.status === 'historical'
              ? ' · Historical'
              : fact.status === 'uncertain'
                ? ' · Uncertain'
                : fact.status === 'conflicted'
                  ? ' · Conflicting information'
                  : ''}
          </Text>
          {editing === fact.knowledge_id ? (
            <TextInput
              value={draft}
              onChangeText={setDraft}
              multiline
              autoFocus
              accessibilityLabel="Edit fact"
              style={{
                color: theme.colors.ink,
                fontSize: 17,
                lineHeight: 25,
                minHeight: 80,
                borderWidth: 1,
                borderColor: theme.colors.teal,
                borderRadius: 12,
                padding: 12,
              }}
            />
          ) : (
            <Text style={{ color: theme.colors.ink, fontSize: 17, lineHeight: 25 }}>
              {fact.statement}
            </Text>
          )}
          {fact.inferred && fact.inference_reason ? (
            <Text style={{ color: theme.colors.mutedInk, lineHeight: 20 }}>
              {fact.inference_reason}
            </Text>
          ) : null}
          <ArchiveEvidence sources={fact.evidence} />
          <View style={{ flexDirection: 'row', gap: 10, flexWrap: 'wrap' }}>
            {editing === fact.knowledge_id ? (
              <>
                <Button
                  label="Save correction"
                  disabled={saving || draft.trim().length < 3}
                  onPress={() => void save(fact)}
                />
                <Button
                  label="Cancel"
                  variant="secondary"
                  disabled={saving}
                  onPress={() => setEditing(null)}
                />
              </>
            ) : (
              <Button
                label="Edit"
                variant="secondary"
                disabled={saving}
                onPress={() => {
                  setEditing(fact.knowledge_id);
                  setDraft(fact.statement);
                }}
              />
            )}
            <Button
              label="Delete"
              variant="secondary"
              disabled={saving}
              onPress={() => remove(fact)}
            />
          </View>
        </Card>
      ))}
      {hasMore ? (
        <Button
          label="Load more"
          variant="secondary"
          disabled={loading}
          onPress={() => void load(facts.length)}
        />
      ) : null}
      <Button
        label="Refresh"
        variant="secondary"
        disabled={loading || saving}
        onPress={() => void load()}
      />
    </View>
  );
}
