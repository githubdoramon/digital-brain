import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import * as FileSystem from 'expo-file-system/legacy';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import React from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Linking,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as Notifications from 'expo-notifications';

import { API_BASE_URL, apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { ScrollHeaderBackdrop } from '@/components/ScrollHeaderBackdrop';
import { useAppNotice } from '@/hooks/useAppNotice';
import {
  copyToDigitalBrainStorage,
  DigitalBrainStorageFolder,
} from '@/storage/digitalBrainStorage';
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
  linked_contacts?: {
    contact_id: string;
    display_name: string;
  }[];
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
  const { token, refreshToken } = useAuth();
  const { showSuccess, showError, showWarning } = useAppNotice();
  const params = useLocalSearchParams<RouteParams>();
  const documentId = Array.isArray(params.documentId) ? params.documentId[0] : params.documentId;
  const scrollY = React.useRef(new Animated.Value(0)).current;

  const [document, setDocument] = React.useState<DocumentDetail | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [isDownloading, setIsDownloading] = React.useState(false);
  const [isDeleting, setIsDeleting] = React.useState(false);

  const ensureNotificationPermission = React.useCallback(async (): Promise<boolean> => {
    const current = await Notifications.getPermissionsAsync();
    if (current.granted) {
      return true;
    }

    const requested = await Notifications.requestPermissionsAsync();
    return requested.granted;
  }, []);

  const notifyDownloadComplete = React.useCallback(
    async (name: string, fileUri: string, mimeType?: string | null) => {
    const allowed = await ensureNotificationPermission();
    if (!allowed) {
      showSuccess(`Downloaded ${name}`);
      return;
    }

    try {
      await Notifications.scheduleNotificationAsync({
        content: {
          title: 'Download complete',
          body: name,
          sound: 'default',
          data: {
            kind: 'document_download',
            fileName: name,
            fileUri,
            mimeType: mimeType || undefined,
          },
        },
        trigger: null,
      });
    } catch {
      showSuccess(`Downloaded ${name}`);
    }
    },
    [ensureNotificationPermission, showSuccess],
  );

  const notifyDownloadFailed = React.useCallback(async (message: string) => {
    const allowed = await ensureNotificationPermission();
    if (!allowed) {
      showError(message);
      return;
    }

    try {
      await Notifications.scheduleNotificationAsync({
        content: {
          title: 'Download failed',
          body: message,
          sound: 'default',
        },
        trigger: null,
      });
    } catch {
      showError(message);
    }
  }, [ensureNotificationPermission, showError]);

  const exportToDigitalBrainStorage = React.useCallback(
    async (localFileUri: string, fileName: string, mimeType?: string | null): Promise<string> =>
      copyToDigitalBrainStorage(
        localFileUri,
        DigitalBrainStorageFolder.Exports,
        fileName,
        mimeType || 'application/octet-stream',
      ),
    [],
  );

  const performDownload = React.useCallback(
    async (endpoint: string, destinationPath: string, initialToken: string) => {
      let activeToken = initialToken;
      let result = await FileSystem.downloadAsync(endpoint, destinationPath, {
        headers: {
          Authorization: `Bearer ${activeToken}`,
        },
      });

      if (result.status === 401) {
        const refreshedToken = await refreshToken();
        if (!refreshedToken) {
          throw new Error('Session expired. Please sign in again.');
        }
        activeToken = refreshedToken;
        result = await FileSystem.downloadAsync(endpoint, destinationPath, {
          headers: {
            Authorization: `Bearer ${activeToken}`,
          },
        });
      }

      if (result.status < 200 || result.status >= 300) {
        throw new Error(`Download failed with status ${result.status}.`);
      }

      return { result, activeToken };
    },
    [refreshToken],
  );

  const loadDocument = React.useCallback(async () => {
    if (!documentId) {
      setError('Missing document id.');
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = (await apiFetch(
        `/mobile/documents/${encodeURIComponent(documentId)}`,
      )) as DocumentDetail;
      setDocument(result);
    } catch (fetchError) {
      const message =
        fetchError instanceof Error ? fetchError.message : 'Failed to load document details.';
      setError(message || 'Failed to load document details.');
    } finally {
      setIsLoading(false);
    }
  }, [documentId]);

  useFocusEffect(
    React.useCallback(() => {
      void loadDocument();
      return undefined;
    }, [loadDocument]),
  );

  const headerOverlayOpacity = React.useMemo(
    () =>
      scrollY.interpolate({
        inputRange: [0, 20, 70],
        outputRange: [0, 0.68, 1],
        extrapolate: 'clamp',
      }),
    [scrollY],
  );

  const handleDownload = React.useCallback(async () => {
    if (!documentId || !document || isDownloading) {
      return;
    }
    if (!token) {
      showError('Session expired. Sign in again.');
      return;
    }

    const downloadEndpoint = `${API_BASE_URL}/mobile/documents/${encodeURIComponent(documentId)}/download`;
    const safeName = (document.file_name || `${documentId}.bin`).replace(/[\\/:*?"<>|]/g, '_');
    const documentDirectory = FileSystem.documentDirectory;
    if (!documentDirectory) {
      showError('Download failed: storage unavailable.');
      return;
    }
    const appTargetPath = `${documentDirectory}${safeName}`;
    const targetPath = appTargetPath;

    setIsDownloading(true);
    try {
      const downloadAttempt = await performDownload(downloadEndpoint, targetPath, token);
      const result = downloadAttempt.result;

      const downloadedInfo = await FileSystem.getInfoAsync(result.uri);
      if (!downloadedInfo.exists) {
        throw new Error('Download reported success, but file was not found on device storage.');
      }

      let openUri = result.uri;
      let completionLabel = safeName;

      if (Platform.OS === 'android') {
        try {
          openUri = await exportToDigitalBrainStorage(result.uri, safeName, document.file_mime);
        } catch (exportError) {
          const message =
            exportError instanceof Error
              ? exportError.message
              : 'Could not save to the Digital Brain folder. File kept in app storage.';
          showWarning(message);
          completionLabel = `${safeName} (saved in app storage)`;
          openUri = await FileSystem.getContentUriAsync(result.uri).catch(() => result.uri);
        }
      }

      await notifyDownloadComplete(completionLabel, openUri, document.file_mime);
      if (Platform.OS !== 'android') {
        void Linking.openURL(openUri).catch(() => undefined);
      }
    } catch (downloadError) {
      const message =
        downloadError instanceof Error ? downloadError.message : 'Failed to download this file.';
      void notifyDownloadFailed(message);
    } finally {
      setIsDownloading(false);
    }
  }, [
    document,
    documentId,
    isDownloading,
    notifyDownloadComplete,
    notifyDownloadFailed,
    exportToDigitalBrainStorage,
    performDownload,
    showError,
    showWarning,
    token,
  ]);

  const handleDelete = React.useCallback(async () => {
    if (!documentId || isDeleting) {
      return;
    }
    setIsDeleting(true);
    try {
      await apiFetch(`/mobile/documents/${encodeURIComponent(documentId)}`, {
        method: 'DELETE',
        token,
      });
      showSuccess('Document deleted.');
      router.back();
    } catch (deleteError) {
      console.warn('[documents] delete failed', deleteError);
      showError('Unable to delete this document right now.');
    } finally {
      setIsDeleting(false);
    }
  }, [documentId, isDeleting, showError, showSuccess, token]);

  const handleConfirmDelete = React.useCallback(() => {
    if (!document || !documentId || isDeleting) {
      return;
    }
    Alert.alert(
      'Delete document?',
      `Delete ${document.title?.trim() || document.file_name || 'this document'}? This action cannot be undone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => {
            void handleDelete();
          },
        },
      ],
    );
  }, [document, documentId, handleDelete, isDeleting]);

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          headerTitle: 'Document',
          headerRight: () => (
            <Pressable
              onPress={() => {
                void handleDownload();
              }}
              disabled={isDownloading}
              style={({ pressed }) => [styles.headerAction, pressed && styles.headerActionPressed]}
              hitSlop={10}
            >
              <Ionicons
                name={isDownloading ? 'hourglass-outline' : 'download-outline'}
                size={20}
                color={theme.colors.ink}
              />
            </Pressable>
          ),
        }}
      />
      <Animated.ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 56, paddingBottom: insets.bottom + 96 },
        ]}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
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
              {document.linked_contacts && document.linked_contacts.length > 0 ? (
                <Text style={styles.bodyText}>
                  Contacts: {document.linked_contacts.map((contact) => contact.display_name).join(', ')}
                </Text>
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

            <Button
              label={isDeleting ? 'Deleting...' : 'Delete document'}
              variant="danger"
              onPress={handleConfirmDelete}
              disabled={isDeleting}
            />
          </>
        ) : null}
      </Animated.ScrollView>

      <ScrollHeaderBackdrop
        height={insets.top + 56}
        opacity={headerOverlayOpacity}
        topAlpha={1}
        bottomAlpha={0.9}
      />

      {documentId && document && !isLoading && !error ? (
        <FloatingSaveButton
          visible
          label="Edit document"
          iconName="create-outline"
          onPress={() =>
            router.push({
              pathname: '/documents/[documentId]/edit',
              params: { documentId },
            })
          }
          bottomOffset={insets.bottom + 20}
        />
      ) : null}
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
