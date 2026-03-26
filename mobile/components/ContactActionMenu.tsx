import React, { useMemo, useState } from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { theme } from '@/theme';

type ContactActionMenuProps = {
  emails: string[];
  phones: string[];
};

const normalizePhone = (phone: string) => phone.replace(/[^0-9+]/g, '');

export function ContactActionMenu({ emails, phones }: ContactActionMenuProps) {
  const [open, setOpen] = useState(false);
  const email = emails[0];
  const phone = phones[0];
  const whatsapp = phone ? normalizePhone(phone) : null;

  const actions = useMemo(
    () =>
      [
        phone
          ? {
              label: 'Call',
              onPress: () => Linking.openURL(`tel:${phone}`),
            }
          : null,
        email
          ? {
              label: 'Email',
              onPress: () => Linking.openURL(`mailto:${email}`),
            }
          : null,
        whatsapp
          ? {
              label: 'WhatsApp',
              onPress: () => Linking.openURL(`https://wa.me/${whatsapp}`),
            }
          : null,
      ].filter(Boolean),
    [email, phone, whatsapp],
  );

  if (actions.length === 0) {
    return null;
  }

  return (
    <>
      <Pressable style={styles.button} onPress={() => setOpen(true)}>
        <Text style={styles.buttonText}>Contact</Text>
      </Pressable>
      <BottomSheet visible={open} onClose={() => setOpen(false)}>
        <View style={styles.sheet}>
          <Text style={styles.sheetTitle}>Reach out</Text>
          {actions.map((action) => (
            <Pressable
              key={action.label}
              onPress={() => {
                setOpen(false);
                action.onPress();
              }}
              style={styles.actionRow}
            >
              <Text style={styles.actionText}>{action.label}</Text>
            </Pressable>
          ))}
        </View>
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: theme.colors.ink,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: theme.radius.lg,
    alignSelf: 'flex-start',
  },
  buttonText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 14,
    letterSpacing: 0.5,
  },
  sheet: {
    paddingBottom: 6,
  },
  sheetTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 12,
  },
  actionRow: {
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  actionText: {
    fontSize: 16,
    color: theme.colors.ink,
    fontWeight: '500',
  },
});
