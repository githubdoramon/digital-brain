import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  FlatList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { useFocusEffect } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import Ionicons from '@expo/vector-icons/Ionicons';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Avatar } from '@/components/Avatar';
import { Card } from '@/components/Card';
import { RelationshipChips } from '@/components/RelationshipChips';
import { theme } from '@/theme';
import { matchesContactSearch } from '@/utils/contactSearch';

type Relationship = {
  relationship_id: string;
  contact_id: string;
  type: string;
  other_type: string | null;
  direction: 'incoming' | 'outgoing';
};

type Contact = {
  contact_id: string;
  display_name: string;
  aliases?: string[];
  emails: string[];
  phones: string[];
  tags: string[];
  comments: string;
  external_id: string | null;
  avatar_url?: string | null;
  relationships: Relationship[];
};

function ContactCard({
  contact,
  subtitle,
  chips,
  onPress,
  index,
  token,
}: {
  contact: Contact;
  subtitle: string;
  chips: { label: string }[];
  onPress: () => void;
  index: number;
  token: string | null;
}) {
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(12)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(opacity, {
        toValue: 1,
        duration: 320,
        delay: 80 + index * 40,
        useNativeDriver: true,
      }),
      Animated.timing(translateY, {
        toValue: 0,
        duration: 320,
        delay: 80 + index * 40,
        useNativeDriver: true,
      }),
    ]).start();
  }, [index, opacity, translateY]);

  return (
    <Animated.View style={[styles.cardPressable, { opacity, transform: [{ translateY }] }]}>
      <Card variant="elevated">
        <Pressable onPress={onPress} style={styles.cardTapArea}>
          <Avatar name={contact.display_name} uri={contact.avatar_url ?? undefined} token={token} />
          <View style={styles.cardBody}>
            <Text style={styles.cardTitle}>{contact.display_name}</Text>
            <Text style={styles.cardSubtitle}>{subtitle}</Text>
            <RelationshipChips chips={chips} />
          </View>
        </Pressable>
      </Card>
    </Animated.View>
  );
}

export default function ContactsScreen() {
  const { token } = useAuth();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const tabBarHeight = useBottomTabBarHeight();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const hasLoadedOnceRef = useRef(false);

  const loadContacts = React.useCallback(
    async ({ showInitialLoader = false, showRefreshSpinner = false } = {}) => {
      const refreshStartedAt = showRefreshSpinner ? Date.now() : null;
      if (showInitialLoader) {
        setIsLoading(true);
      }
      if (showRefreshSpinner) {
        setIsRefreshing(true);
      }
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        setContacts(result.contacts ?? []);
      } catch (error) {
        console.warn('[contacts] load failed', error);
      } finally {
        if (refreshStartedAt !== null) {
          const elapsed = Date.now() - refreshStartedAt;
          const minVisibleMs = 450;
          if (elapsed < minVisibleMs) {
            await new Promise((resolve) => setTimeout(resolve, minVisibleMs - elapsed));
          }
        }
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [],
  );

  useFocusEffect(
    React.useCallback(() => {
      void loadContacts({ showInitialLoader: !hasLoadedOnceRef.current });
      hasLoadedOnceRef.current = true;
      return undefined;
    }, [loadContacts]),
  );

  const handleRefresh = React.useCallback(() => {
    void loadContacts({ showRefreshSpinner: true });
  }, [loadContacts]);

  const contactMap = useMemo(() => {
    const map = new Map<string, string>();
    contacts.forEach((contact) => map.set(contact.contact_id, contact.display_name));
    return map;
  }, [contacts]);

  const filtered = useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return contacts;
    return contacts.filter((contact) => matchesContactSearch(contact, trimmed));
  }, [contacts, query]);

  const listHeader = (
    <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
      <Text style={styles.kicker}>Contacts</Text>
      <Text style={styles.title}>People you (may) care about</Text>
      <View style={styles.searchWrap}>
        <TextInput
          placeholder="Search contacts"
          placeholderTextColor={theme.colors.mutedInk}
          value={query}
          onChangeText={setQuery}
          style={styles.searchInput}
        />
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <FlatList
        data={filtered}
        keyExtractor={(item) => item.contact_id}
        refreshing={isRefreshing}
        onRefresh={handleRefresh}
        progressViewOffset={insets.top + 16}
        contentContainerStyle={[
          styles.listContent,
          {
            paddingBottom: insets.bottom + tabBarHeight + 122,
          },
        ]}
        ListHeaderComponent={listHeader}
        ListEmptyComponent={
          !isLoading ? <Text style={styles.empty}>No contacts found.</Text> : null
        }
        renderItem={({ item, index }) => {
          const subtitle = item.emails?.[0] || item.phones?.[0] || 'No primary contact info yet';
          const chips = (item.relationships || [])
            .slice(0, 3)
            .map((rel) => {
              const name = contactMap.get(rel.contact_id) ?? 'Unknown';
              return { label: `${rel.type} · ${name}` };
            });
          return (
            <ContactCard
              contact={item}
              subtitle={subtitle}
              chips={chips}
              index={index}
              token={token}
              onPress={() =>
                router.push({
                  pathname: '/contacts/[contactId]',
                  params: { contactId: item.contact_id },
                })
              }
            />
          );
        }}
      />
      <Pressable
        onPress={() => router.push('/contacts/new')}
        accessibilityRole="button"
        accessibilityLabel="Add a contact"
        style={({ pressed }) => [
          styles.fab,
          { bottom: insets.bottom + tabBarHeight + 24 },
          pressed && styles.fabPressed,
        ]}
      >
        <Ionicons name="add" size={26} color="#fff" />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  listContent: {
    paddingHorizontal: 20,
    gap: 16,
  },
  header: {
    paddingBottom: 12,
  },
  kicker: {
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 3,
    color: theme.colors.teal,
    fontWeight: '600',
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
    marginTop: 6,
  },
  subtitle: {
    marginTop: 6,
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  searchWrap: {
    marginTop: 16,
  },
  searchInput: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    borderRadius: theme.radius.lg,
    paddingHorizontal: 16,
    paddingVertical: 12,
    color: theme.colors.ink,
  },
  cardTapArea: {
    flexDirection: 'row',
    gap: 14,
    padding: 16,
    borderRadius: theme.radius.lg,
  },
  cardPressable: {
    alignSelf: 'stretch',
  },
  cardBody: {
    flex: 1,
    gap: 6,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  cardSubtitle: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  empty: {
    fontSize: 14,
    color: theme.colors.mutedInk,
    textAlign: 'center',
    marginTop: 24,
  },
  fab: {
    position: 'absolute',
    right: 22,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: '#0f1113',
    shadowOpacity: 0.38,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: 16 },
    elevation: 14,
  },
  fabPressed: {
    transform: [{ scale: 0.97 }],
    shadowOpacity: 0.18,
  },
});
