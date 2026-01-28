import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { Avatar } from '@/components/Avatar';
import { RelationshipChips } from '@/components/RelationshipChips';
import { theme } from '@/theme';

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
  emails: string[];
  phones: string[];
  tags: string[];
  comments: string;
  external_id: string | null;
  avatar_url?: string | null;
  relationships: Relationship[];
};

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

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
    <AnimatedPressable
      style={[styles.card, { opacity, transform: [{ translateY }] }]}
      onPress={onPress}
    >
      <Avatar name={contact.display_name} uri={contact.avatar_url ?? undefined} token={token} />
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle}>{contact.display_name}</Text>
        <Text style={styles.cardSubtitle}>{subtitle}</Text>
        <RelationshipChips chips={chips} />
      </View>
    </AnimatedPressable>
  );
}

export default function ContactsScreen() {
  const { token } = useAuth();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        if (mounted) {
          setContacts(result.contacts ?? []);
        }
      } catch (error) {
        console.warn('[contacts] load failed', error);
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const contactMap = useMemo(() => {
    const map = new Map<string, string>();
    contacts.forEach((contact) => map.set(contact.contact_id, contact.display_name));
    return map;
  }, [contacts]);

  const filtered = useMemo(() => {
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) return contacts;
    return contacts.filter((contact) => contact.display_name.toLowerCase().includes(trimmed));
  }, [contacts, query]);

  const listHeader = (
    <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
      <Text style={styles.kicker}>Contacts</Text>
      <Text style={styles.title}>People you care about</Text>
      <Text style={styles.subtitle}>Search, edit, and keep track of relationships.</Text>
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
        contentContainerStyle={styles.listContent}
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
              onPress={() => router.push(`/contacts/${encodeURIComponent(item.contact_id)}`)}
            />
          );
        }}
      />
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
    paddingBottom: 40,
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
  card: {
    flexDirection: 'row',
    gap: 14,
    padding: 16,
    borderRadius: theme.radius.lg,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.line,
    shadowColor: theme.shadow.color,
    shadowOpacity: theme.shadow.opacity,
    shadowRadius: theme.shadow.radius,
    shadowOffset: theme.shadow.offset,
    elevation: 2,
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
});
