import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import React from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Linking,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';

type Provider = 'whatsapp' | 'telegram';
type Identity = {
  identity_id: string;
  external_id: string;
  display_name: string;
  source_username: string | null;
  source_phone: string | null;
  contact_id: string | null;
  contact_name: string | null;
  source_link?: { url: string; label: string; target: 'conversation' | 'message' } | null;
};
type LinkedIdentity = Identity & { provider: Provider };
const providers: Provider[] = ['whatsapp', 'telegram'];
const providerName = (provider: Provider) => (provider === 'whatsapp' ? 'WhatsApp' : 'Telegram');
const identityDetails = (identity: Identity) =>
  identity.source_username
    ? `@${identity.source_username}`
    : identity.source_phone || identity.external_id.replace(/@s\.whatsapp\.net$/, '');
const pageSize = 50;

async function fetchIdentities(provider: Provider, params: Record<string, string>) {
  const query = new URLSearchParams({ provider, ...params });
  const result = (await apiFetch(`/mobile/messaging/identities?${query}`)) as {
    identities: Identity[];
  };
  return result.identities;
}

export function MessengerLinksCard({
  contactId,
  contactName,
}: {
  contactId: string;
  contactName: string;
}) {
  const { showSuccess, showError } = useAppNotice();
  const [linked, setLinked] = React.useState<LinkedIdentity[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState(false);
  const [provider, setProvider] = React.useState<Provider | null>(null);
  const [query, setQuery] = React.useState('');
  const [results, setResults] = React.useState<Identity[]>([]);
  const [searching, setSearching] = React.useState(false);
  const [searchError, setSearchError] = React.useState(false);
  const [hasMore, setHasMore] = React.useState(false);
  const [offset, setOffset] = React.useState(0);
  const [retry, setRetry] = React.useState(0);
  const [saving, setSaving] = React.useState(false);
  const searchVersion = React.useRef(0);
  const loadVersion = React.useRef(0);
  const mutationPending = React.useRef(false);

  const loadLinks = React.useCallback(async () => {
    const version = ++loadVersion.current;
    setLoading(true);
    setLoadError(false);
    try {
      const groups = await Promise.all(
        providers.map(async (value) => {
          const identities: LinkedIdentity[] = [];
          // Linked accounts are fetched independently of search and its first page.
          for (let start = 0; ; start += 500) {
            const page = await fetchIdentities(value, {
              contact_id: contactId,
              limit: '500',
              offset: String(start),
            });
            if (version !== loadVersion.current) return [];
            identities.push(...page.map((identity) => ({ ...identity, provider: value })));
            if (page.length < 500) return identities;
          }
        }),
      );
      if (version === loadVersion.current) setLinked(groups.flat());
    } catch {
      if (version === loadVersion.current) setLoadError(true);
    } finally {
      if (version === loadVersion.current) setLoading(false);
    }
  }, [contactId]);

  useFocusEffect(
    React.useCallback(() => {
      void loadLinks();
      return () => {
        loadVersion.current += 1;
        searchVersion.current += 1;
      };
    }, [loadLinks]),
  );

  React.useEffect(() => {
    const version = ++searchVersion.current;
    if (!provider) return;
    setSearching(true);
    setSearchError(false);
    if (!offset) {
      setResults([]);
      setHasMore(false);
    }
    const timer = setTimeout(() => {
      void fetchIdentities(provider, {
        q: query.trim(),
        limit: String(pageSize),
        offset: String(offset),
      })
        .then((page) => {
          if (version !== searchVersion.current) return;
          setResults((current) => (offset ? [...current, ...page] : page));
          setHasMore(page.length === pageSize);
        })
        .catch(() => {
          if (version === searchVersion.current) setSearchError(true);
        })
        .finally(() => {
          if (version === searchVersion.current) setSearching(false);
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      searchVersion.current += 1;
    };
  }, [provider, query, offset, retry]);

  const updateLink = async (identity: Identity, source: Provider, target: string | null) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    setSaving(true);
    try {
      await apiFetch(
        `/mobile/messaging/identities/${encodeURIComponent(identity.identity_id)}/contact?provider=${source}`,
        {
          method: 'PUT',
          body: JSON.stringify({ contact_id: target, expected_contact_id: identity.contact_id }),
        },
      );
      setProvider(null);
      await loadLinks();
      showSuccess(target ? 'Messenger account linked.' : 'Messenger account unlinked.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Unable to update this link. Try again.');
      setOffset(0);
      setRetry((value) => value + 1);
      void loadLinks();
    } finally {
      mutationPending.current = false;
      setSaving(false);
    }
  };

  const choose = (identity: Identity) => {
    if (!provider || identity.contact_id === contactId) return;
    const source = provider;
    if (identity.contact_id) {
      Alert.alert(
        'Move messenger link?',
        `${identity.display_name || identityDetails(identity)} is linked to ${identity.contact_name || 'another contact'}. Move it to ${contactName}?`,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Move link', onPress: () => void updateLink(identity, source, contactId) },
        ],
      );
    } else {
      void updateLink(identity, source, contactId);
    }
  };

  return (
    <Card style={styles.card}>
      <Text style={styles.title}>Messenger links</Text>
      {loading ? (
        <Text style={styles.muted}>Loading linked accounts…</Text>
      ) : loadError ? (
        <>
          <Text style={styles.muted}>Unable to load linked accounts.</Text>
          <Button label="Try again" variant="secondary" onPress={() => void loadLinks()} />
        </>
      ) : linked.length ? (
        linked.map((identity) => (
          <View key={`${identity.provider}:${identity.identity_id}`} style={styles.row}>
            <Pressable
              accessibilityRole="link"
              accessibilityLabel={
                identity.source_link
                  ? `${identity.source_link.label}: ${identity.display_name || contactName}`
                  : identity.display_name || providerName(identity.provider)
              }
              disabled={!identity.source_link}
              onPress={() => {
                if (!identity.source_link) return;
                void Linking.openURL(identity.source_link.url).catch(() =>
                  showError(
                    `Unable to open ${providerName(identity.provider)}. Try opening the app directly.`,
                  ),
                );
              }}
              style={styles.accountLink}
            >
              <Ionicons
                name={identity.provider === 'whatsapp' ? 'logo-whatsapp' : 'paper-plane-outline'}
                size={20}
                color={theme.colors.teal}
              />
              <View style={styles.copy}>
                <Text style={styles.name}>
                  {identity.display_name || providerName(identity.provider)}
                </Text>
                <Text style={styles.muted}>
                  {providerName(identity.provider)} · {identityDetails(identity)}
                </Text>
              </View>
              {identity.source_link ? (
                <Ionicons name="open-outline" size={18} color={theme.colors.teal} />
              ) : null}
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Unlink ${identity.display_name || providerName(identity.provider)}`}
              disabled={saving}
              onPress={() =>
                Alert.alert('Unlink messenger account?', `Remove this link from ${contactName}?`, [
                  { text: 'Cancel', style: 'cancel' },
                  {
                    text: 'Unlink',
                    style: 'destructive',
                    onPress: () => void updateLink(identity, identity.provider, null),
                  },
                ])
              }
              style={styles.unlink}
            >
              <Ionicons name="unlink-outline" size={22} color={theme.colors.accentDeep} />
            </Pressable>
          </View>
        ))
      ) : (
        <Text style={styles.muted}>No messenger accounts linked yet.</Text>
      )}
      {providers.map((value) => (
        <Button
          key={value}
          label={`Link ${providerName(value)} account`}
          variant="secondary"
          disabled={saving}
          onPress={() => {
            setQuery('');
            setOffset(0);
            setResults([]);
            setProvider(value);
          }}
        />
      ))}
      <BottomSheet
        visible={!!provider}
        sheetStyle={{ height: '80%' }}
        contentStyle={{ flex: 1 }}
        onClose={() => {
          if (!saving) setProvider(null);
        }}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
          style={styles.picker}
        >
          <Text style={styles.title}>
            Link {provider ? providerName(provider) : 'messenger'} account
          </Text>
          <Text style={styles.muted}>
            Choose an account from your messaging archive for {contactName}.
          </Text>
          <TextInput
            accessibilityLabel="Search messenger accounts"
            placeholder="Search name, username or phone"
            value={query}
            maxLength={200}
            editable={!saving}
            onChangeText={(value) => {
              setQuery(value);
              setOffset(0);
            }}
            style={styles.input}
            autoCapitalize="none"
            autoCorrect={false}
          />
          <ScrollView
            automaticallyAdjustKeyboardInsets
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="on-drag"
            contentContainerStyle={styles.options}
          >
            {results.map((identity) => (
              <Pressable
                key={identity.identity_id}
                accessibilityRole="button"
                disabled={saving || searching || identity.contact_id === contactId}
                onPress={() => choose(identity)}
                style={styles.option}
              >
                <View style={styles.copy}>
                  <Text style={styles.name}>
                    {identity.display_name || identityDetails(identity)}
                  </Text>
                  <Text style={styles.muted}>{identityDetails(identity)}</Text>
                  {identity.contact_id ? (
                    <Text style={styles.muted}>
                      {identity.contact_id === contactId
                        ? 'Linked to this contact'
                        : `Linked to ${identity.contact_name || 'another contact'}`}
                    </Text>
                  ) : null}
                </View>
                <Ionicons
                  name={identity.contact_id === contactId ? 'checkmark' : 'add'}
                  size={22}
                  color={theme.colors.teal}
                />
              </Pressable>
            ))}
            {saving ? (
              <Text style={styles.muted}>Updating link…</Text>
            ) : searching ? (
              <Text style={styles.muted}>Searching…</Text>
            ) : searchError ? (
              <Button
                label="Retry search"
                variant="secondary"
                onPress={() => setRetry((value) => value + 1)}
              />
            ) : !results.length ? (
              <Text style={styles.muted}>No accounts found. Try another name or number.</Text>
            ) : null}
            {hasMore && !searchError ? (
              <Button
                label="Load more"
                variant="secondary"
                disabled={searching || saving}
                onPress={() => setOffset((value) => value + pageSize)}
              />
            ) : null}
          </ScrollView>
          <Button
            label="Back"
            variant="secondary"
            disabled={saving}
            onPress={() => setProvider(null)}
          />
        </KeyboardAvoidingView>
      </BottomSheet>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: 16, padding: 18, gap: 12 },
  title: { fontSize: 18, fontWeight: '700', color: theme.colors.ink },
  name: { fontSize: 15, fontWeight: '600', color: theme.colors.ink },
  muted: { fontSize: 13, lineHeight: 19, color: theme.colors.mutedInk },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  accountLink: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
  },
  copy: { flex: 1, gap: 4 },
  unlink: { padding: 12 },
  picker: { flex: 1, gap: 12 },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    padding: 12,
    fontSize: 15,
    color: theme.colors.ink,
  },
  options: { paddingBottom: 20, gap: 10 },
  option: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
  },
});
