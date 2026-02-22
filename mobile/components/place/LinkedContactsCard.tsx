import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React, { useState } from 'react';
import { Alert, StyleSheet, Text, View } from 'react-native';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

type LinkedContact = {
  contact_id: string;
  display_name: string | null;
  role: string | null;
};

export function LinkedContactsCard({ placeId }: { placeId: string }) {
  const router = useRouter();
  const [contacts, setContacts] = useState<LinkedContact[]>([]);
  const [isSaving, setIsSaving] = useState(false);

  const load = React.useCallback(async () => {
    try {
      const result = (await apiFetch(
        `/mobile/places/${encodeURIComponent(placeId)}/contacts`,
      )) as { contacts: LinkedContact[] };
      setContacts(result.contacts || []);
    } catch (error) {
      console.warn('[place-contacts] load failed', error);
    }
  }, [placeId]);

  useFocusEffect(
    React.useCallback(() => {
      void load();
      return undefined;
    }, [load]),
  );

  const handleRemove = async (contactId: string) => {
    setIsSaving(true);
    try {
      await apiFetch(
        `/mobile/places/${encodeURIComponent(placeId)}/contacts/${encodeURIComponent(contactId)}`,
        {
          method: 'DELETE',
        },
      );
      await load();
    } catch (error) {
      console.warn('[place-contacts] remove failed', error);
      Alert.alert('Failed to unlink contact', 'Could not remove this contact from the place.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card style={styles.section}>
      <Text style={styles.sectionTitle}>Linked people</Text>
      {contacts.length === 0 ? <Text style={styles.empty}>No linked people yet.</Text> : null}

      {contacts.map((contact) => (
        <View key={contact.contact_id} style={styles.row}>
          <Pressable
            style={styles.contactTapArea}
            onPress={() =>
              router.push({
                pathname: '/contacts/[contactId]',
                params: { contactId: contact.contact_id },
              })
            }
          >
            <Text style={styles.contactName}>{contact.display_name || contact.contact_id}</Text>
            <Text style={styles.contactMeta}>{contact.role?.trim() || 'Linked contact'}</Text>
          </Pressable>

          <Pressable
            onPress={() => void handleRemove(contact.contact_id)}
            disabled={isSaving}
            style={({ pressed }) => [
              styles.removeIconButton,
              (pressed || isSaving) && styles.removeIconButtonPressed,
            ]}
            accessibilityRole="button"
            accessibilityLabel={`Remove ${contact.display_name || 'contact'} from place`}
            hitSlop={6}
          >
            <Ionicons name="close" size={20} color="#b83f35" />
          </Pressable>
        </View>
      ))}
    </Card>
  );
}

const styles = StyleSheet.create({
  section: {
    gap: 10,
    padding: 16,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  empty: {
    fontSize: 13,
    color: theme.colors.mutedInk,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.line,
    paddingTop: 10,
  },
  contactTapArea: {
    flex: 1,
    gap: 2,
  },
  contactName: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  contactMeta: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  removeIconButton: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: '#efd0ca',
    backgroundColor: '#fff4f2',
  },
  removeIconButtonPressed: {
    opacity: 0.72,
    transform: [{ scale: 0.98 }],
  },
});
