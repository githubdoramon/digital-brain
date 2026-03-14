import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React from 'react';
import { ActivityIndicator, Linking, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';

import { API_BASE_URL } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type RouteParams = {
  documentId?: string;
};

export default function DocumentFileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { token } = useAuth();
  const params = useLocalSearchParams<RouteParams>();
  const documentId = Array.isArray(params.documentId) ? params.documentId[0] : params.documentId;
  const [isLoading, setIsLoading] = React.useState(true);

  const downloadUrl = React.useMemo(() => {
    if (!documentId) return '';
    return `${API_BASE_URL}/mobile/documents/${encodeURIComponent(documentId)}/download`;
  }, [documentId]);

  const openExternal = React.useCallback(async () => {
    if (!downloadUrl) return;
    try {
      await Linking.openURL(downloadUrl);
    } catch {
      // no-op
    }
  }, [downloadUrl]);

  return (
    <View style={styles.container}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}> 
        <Pressable onPress={() => router.back()} style={styles.iconButton}>
          <Ionicons name="chevron-back" size={22} color={theme.colors.ink} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1}>
          Document file
        </Text>
        <Pressable onPress={openExternal} style={styles.iconButton}>
          <Ionicons name="open-outline" size={18} color={theme.colors.ink} />
        </Pressable>
      </View>

      {!downloadUrl ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyText}>Missing document id.</Text>
        </View>
      ) : !token ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyText}>Sign in again to load the file.</Text>
        </View>
      ) : (
        <View style={styles.viewerWrap}>
          <WebView
            source={{
              uri: downloadUrl,
              headers: {
                Authorization: `Bearer ${token}`,
              },
            }}
            startInLoadingState
            onLoadEnd={() => setIsLoading(false)}
          />
          {isLoading ? (
            <View style={styles.loadingOverlay}>
              <ActivityIndicator color={theme.colors.accentDeep} />
            </View>
          ) : null}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  topBar: {
    paddingHorizontal: 12,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
    backgroundColor: theme.colors.card,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  iconButton: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f4eee5',
  },
  title: {
    flex: 1,
    color: theme.colors.ink,
    fontSize: 16,
    fontWeight: '700',
  },
  viewerWrap: {
    flex: 1,
  },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(247,242,236,0.45)',
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  emptyText: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
  },
});
