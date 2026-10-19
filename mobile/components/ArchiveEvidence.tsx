import React from 'react';
import { ScrollView, Text, View } from 'react-native';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { BottomSheet } from '@/components/BottomSheet';
import { Button } from '@/components/Button';
import { theme } from '@/theme';

export type ArchiveSource = {
  source_id: number;
  part?: number;
  quote: string;
  sent_at?: string | null;
};

export function ArchiveEvidence({ sources }: { sources: ArchiveSource[] }) {
  const { token } = useAuth();
  const [visible, setVisible] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [source, setSource] = React.useState<{
    title?: string;
    text?: string;
    sent_at?: string;
    current?: boolean;
    speaker?: string | null;
  } | null>(null);
  const [error, setError] = React.useState(false);

  async function open(id: number, part = 0) {
    setSource(null);
    setError(false);
    setLoading(true);
    setVisible(true);
    try {
      const result = await apiFetch(`/mobile/intelligence/sources/${id}?part=${part}`, { token });
      setSource(result as typeof source);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={{ gap: 8 }}>
      {sources.slice(-3).map((item, index) => (
        <View key={`${item.source_id}-${index}`} style={{ gap: 4 }}>
          <Text style={{ color: theme.colors.ink }}>“{item.quote}”</Text>
          <Button
            label={
              item.sent_at
                ? `View source · ${new Date(item.sent_at).toLocaleDateString()}`
                : 'View source'
            }
            variant="secondary"
            onPress={() => void open(item.source_id, item.part)}
          />
        </View>
      ))}
      <BottomSheet visible={visible} onClose={() => setVisible(false)}>
        <ScrollView style={{ maxHeight: 440 }} contentContainerStyle={{ padding: 20, gap: 12 }}>
          <Text style={{ fontSize: 20, color: theme.colors.ink }}>{source?.title || 'Source'}</Text>
          {source?.speaker ? <Text>{source.speaker}</Text> : null}
          {loading ? (
            <Text>Loading…</Text>
          ) : error ? (
            <Text>Could not load this source.</Text>
          ) : (
            <>
              {source?.current === false ? (
                <Text>This source has changed since this information was learned.</Text>
              ) : null}
              <Text style={{ color: theme.colors.ink }}>{source?.text}</Text>
            </>
          )}
          <Button label="Close" variant="secondary" onPress={() => setVisible(false)} />
        </ScrollView>
      </BottomSheet>
    </View>
  );
}
