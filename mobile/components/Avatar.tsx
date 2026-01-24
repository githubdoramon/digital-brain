import React, { useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

import { API_BASE_URL } from '@/api/client';
import { theme } from '@/theme';

type AvatarProps = {
  name: string;
  uri?: string | null;
  size?: number;
  token?: string | null;
};

const getInitials = (name: string) => {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return `${parts[0]![0]}${parts[parts.length - 1]![0]}`.toUpperCase();
};

export function Avatar({ name, uri, size = 56, token }: AvatarProps) {
  const initials = getInitials(name || '');
  const [hasError, setHasError] = useState(false);

  if (uri && !hasError) {
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return (
      <Image
        source={{
          uri: uri.startsWith('http') ? uri : `${API_BASE_URL}${uri}`,
          headers: Object.keys(headers).length ? headers : undefined,
        }}
        style={[styles.image, { width: size, height: size, borderRadius: size / 2 }]}
        onError={() => setHasError(true)}
      />
    );
  }

  return (
    <View style={[styles.fallback, { width: size, height: size, borderRadius: size / 2 }]}>
      <Text style={styles.initials}>{initials}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  image: {
    backgroundColor: theme.colors.paleTeal,
  },
  fallback: {
    backgroundColor: theme.colors.paleTeal,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  initials: {
    fontSize: 18,
    fontWeight: '700',
    color: theme.colors.teal,
  },
});
