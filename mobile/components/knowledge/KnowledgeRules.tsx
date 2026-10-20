import React from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { Alert, Text, View } from 'react-native';
import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type Rule = { rule_id: string; content: string };
export function KnowledgeRules() {
  const { token } = useAuth();
  const { showError } = useAppNotice();
  const [rules, setRules] = React.useState<Rule[]>([]);
  const [busy, setBusy] = React.useState(false);
  useFocusEffect(
    React.useCallback(() => {
      let active = true;
      void (apiFetch('/mobile/rules', { token }) as Promise<{ rules: Rule[] }>)
        .then((result) => {
          if (active) setRules(result.rules);
        })
        .catch(() => {
          if (active) showError('Could not load your instructions.');
        });
      return () => {
        active = false;
      };
    }, [showError, token]),
  );
  const remove = (rule: Rule) =>
    Alert.alert('Remove instruction?', rule.content, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: async () => {
          setBusy(true);
          try {
            await apiFetch(`/mobile/rules/${encodeURIComponent(rule.rule_id)}`, {
              token,
              method: 'DELETE',
            });
            setRules((previous) => previous.filter((item) => item.rule_id !== rule.rule_id));
          } catch {
            showError('Could not remove this instruction.');
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
  return rules.length ? (
    <Card style={{ padding: 20, gap: 14 }}>
      <Text style={{ color: theme.colors.ink, fontSize: 18, fontWeight: '600' }}>
        Your instructions
      </Text>
      {rules.map((rule) => (
        <View key={rule.rule_id} style={{ gap: 10 }}>
          <Text style={{ color: theme.colors.ink, fontSize: 16, lineHeight: 24 }}>
            {rule.content}
          </Text>
          <Button
            label="Remove instruction"
            variant="secondary"
            disabled={busy}
            onPress={() => remove(rule)}
          />
        </View>
      ))}
    </Card>
  ) : null;
}
