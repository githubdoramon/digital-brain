import Ionicons from '@expo/vector-icons/Ionicons';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

type RouteParams = {
  documentId?: string;
};

type DocumentDetail = {
  document_id: string;
  title: string;
  tags?: string[];
  description?: string | null;
  document_date?: string | null;
  file_name: string;
  file_mime?: string | null;
  file_size?: number | null;
  content_preview?: string | null;
  created_at?: string;
  updated_at?: string;
};

function formatDate(value?: string | null): string {
  if (!value) return 'Unknown';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatFileSize(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return 'Unknown size';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export default function DocumentDetailScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const params = useLocalSearchParams<RouteParams>();
  const documentId = Array.isArray(params.documentId) ? params.documentId[0] : params.documentId;

  const [document, setDocument] = React.useState<DocumentDetail | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let mounted = true;
    if (!documentId) {
      setError('Missing document id.');
      setIsLoading(false);
      return () => undefined;
    }

    (async () => {
      try {
        const result = (await apiFetch(
          `/mobile/documents/${encodeURIComponent(documentId)}`,
        )) as DocumentDetail;
        if (!mounted) return;
        setDocument(result);
      } catch (fetchError) {
        if (!mounted) return;
        const message =
          fetchError instanceof Error ? fetchError.message : 'Failed to load document details.';
        setError(message || 'Failed to load document details.');
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      mounted = false;
    };
  }, [documentId]);

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          headerTitle: 'Document',
          headerRight: () => (
            <Pressable
              onPress={() => {
                if (!documentId) return;
                router.push(`/documents/${encodeURIComponent(documentId)}/file`);
              }}
              style={({ pressed }) => [styles.headerAction, pressed && styles.headerActionPressed]}
              hitSlop={10}
            >
              <Ionicons name="download-outline" size={20} color={theme.colors.ink} />
            </Pressable>
          ),
        }}
      />
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 56, paddingBottom: insets.bottom + 24 },
        ]}
      >
        {isLoading ? (
          <View style={styles.centerState}>
            <ActivityIndicator color={theme.colors.accentDeep} />
            <Text style={styles.stateText}>Loading document...</Text>
          </View>
        ) : error ? (
          <Card style={styles.errorCard}>
            <Text style={styles.errorTitle}>Could not load document</Text>
            <Text style={styles.errorText}>{error}</Text>
          </Card>
        ) : document ? (
          <>
            <Card variant="elevated" style={styles.headerCard}>
              <Text style={styles.title}>{document.title || 'Untitled document'}</Text>
              <Text style={styles.metaText}>{document.file_name}</Text>
              <Text style={styles.metaText}>
                {formatFileSize(document.file_size)} {document.file_mime ? `- ${document.file_mime}` : ''}
              </Text>
            </Card>

            <Card style={styles.sectionCard}>
              <Text style={styles.sectionTitle}>Details</Text>
              <Text style={styles.bodyText}>Document date: {formatDate(document.document_date)}</Text>
              <Text style={styles.bodyText}>Created: {formatDate(document.created_at)}</Text>
              <Text style={styles.bodyText}>Updated: {formatDate(document.updated_at)}</Text>
              {document.tags && document.tags.length > 0 ? (
                <Text style={styles.bodyText}>Tags: {document.tags.join(', ')}</Text>
              ) : null}
              {document.description ? <Text style={styles.bodyText}>{document.description}</Text> : null}
            </Card>

            {document.content_preview ? (
              <Card style={styles.sectionCard}>
                <Text style={styles.sectionTitle}>Preview</Text>
                <Text selectable style={styles.previewText}>
                  {document.content_preview}
                </Text>
              </Card>
            ) : null}
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  content: {
    paddingHorizontal: 16,
    gap: 12,
  },
  headerAction: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f4eee5',
  },
  headerActionPressed: {
    opacity: 0.75,
  },
  centerState: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 32,
    gap: 10,
  },
  stateText: {
    color: theme.colors.mutedInk,
    fontSize: 14,
  },
  errorCard: {
    padding: 14,
    gap: 8,
  },
  errorTitle: {
    color: theme.colors.accentDeep,
    fontWeight: '700',
    fontSize: 16,
  },
  errorText: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 20,
  },
  headerCard: {
    padding: 16,
    gap: 6,
  },
  title: {
    color: theme.colors.ink,
    fontSize: 20,
    lineHeight: 26,
    fontWeight: '700',
  },
  metaText: {
    color: theme.colors.mutedInk,
    fontSize: 13,
    lineHeight: 18,
  },
  sectionCard: {
    padding: 14,
    gap: 8,
  },
  sectionTitle: {
    color: theme.colors.ink,
    fontSize: 16,
    fontWeight: '700',
  },
  bodyText: {
    color: theme.colors.ink,
    fontSize: 14,
    lineHeight: 20,
  },
  previewText: {
    color: theme.colors.ink,
    fontSize: 13,
    lineHeight: 20,
  },
});
