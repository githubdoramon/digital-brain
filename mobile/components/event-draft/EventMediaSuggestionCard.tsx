import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { Image, ScrollView, StyleSheet, Text, View } from 'react-native';

import { API_BASE_URL } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { type EventPhoto } from '@/components/event-draft/types';
import { appendEventPhotoDebugLog } from '@/debug/eventPhotoDebugLog';
import { theme } from '@/theme';

type EventMediaSuggestionCardProps = {
  suggestions: EventPhoto[];
  editable?: boolean;
  token?: string | null;
  onRemove?: (assetId: string) => void;
};

function suggestionMeta(media: EventPhoto): string {
  const kind = media.media_type === 'video' ? 'Video' : 'Photo';
  const capturedAt = media.captured_at ? new Date(media.captured_at) : null;
  const date =
    capturedAt && !Number.isNaN(capturedAt.getTime())
      ? capturedAt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      : null;
  const duration =
    media.duration_seconds && media.duration_seconds > 0
      ? `${Math.round(media.duration_seconds)}s`
      : null;
  return [kind, date, duration].filter(Boolean).join(' · ');
}

export function EventMediaSuggestionCard({
  suggestions,
  editable = true,
  token,
  onRemove,
}: EventMediaSuggestionCardProps) {
  const headers = React.useMemo(
    () => (token ? { Authorization: `Bearer ${token}` } : undefined),
    [token],
  );
  const lastRenderedSignature = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (suggestions.length === 0) return;
    const signature = suggestions
      .map((suggestion) => `${suggestion.asset_id}:${suggestion.thumbnail_path || ''}`)
      .join('|');
    if (signature === lastRenderedSignature.current) return;
    lastRenderedSignature.current = signature;
    void appendEventPhotoDebugLog('event-media-suggestions-rendered', {
      count: suggestions.length,
      assetIds: suggestions.map((suggestion) => suggestion.asset_id),
      capturedAt: suggestions.map((suggestion) => suggestion.captured_at),
      thumbnailPaths: suggestions.map((suggestion) => suggestion.thumbnail_path),
    });
  }, [suggestions]);

  if (suggestions.length === 0) return null;

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <View style={styles.headerCopy}>
          <Text style={styles.label}>Suggested media</Text>
          <Text style={styles.helperText}>
            Matched by time, with nearby location used when available.
          </Text>
        </View>
        <Text style={styles.count}>{suggestions.length}</Text>
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
      >
        {suggestions.map((media) => {
          const thumbnailUri = media.thumbnail_path
            ? `${API_BASE_URL}${media.thumbnail_path}`
            : null;
          return (
            <View key={media.asset_id} style={styles.item}>
              {thumbnailUri ? (
                <Image
                  source={{ uri: thumbnailUri, headers }}
                  style={styles.image}
                  resizeMode="cover"
                  onLoad={() => {
                    void appendEventPhotoDebugLog('event-media-thumbnail-loaded', {
                      assetId: media.asset_id,
                    });
                  }}
                  onError={(event) => {
                    void appendEventPhotoDebugLog('event-media-thumbnail-error', {
                      assetId: media.asset_id,
                      error: event.nativeEvent.error,
                    });
                  }}
                />
              ) : (
                <View style={[styles.image, styles.fallback]}>
                  <Ionicons name="images-outline" size={24} color={theme.colors.mutedInk} />
                </View>
              )}
              {media.media_type === 'video' ? (
                <View style={styles.videoBadge}>
                  <Ionicons name="videocam" size={12} color="#fff" />
                </View>
              ) : null}
              {editable && onRemove ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Remove suggested media"
                  onPress={() => onRemove(media.asset_id)}
                  style={({ pressed }) => [
                    styles.removeButton,
                    pressed && styles.removeButtonPressed,
                  ]}
                >
                  <Ionicons name="close" size={14} color="#fff" />
                </Pressable>
              ) : null}
              <Text style={styles.meta}>{suggestionMeta(media)}</Text>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 10 },
  headerRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  headerCopy: { flex: 1, gap: 4 },
  label: { color: theme.colors.ink, fontSize: 13, fontWeight: '600' },
  helperText: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 17 },
  count: { color: theme.colors.teal, fontSize: 13, fontWeight: '700' },
  row: { gap: 10, paddingRight: 4 },
  item: { width: 132, gap: 5, position: 'relative' },
  image: { width: 132, height: 98, borderRadius: theme.radius.md, backgroundColor: '#fff' },
  fallback: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  videoBadge: {
    position: 'absolute',
    left: 7,
    top: 7,
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(20, 28, 36, 0.72)',
  },
  removeButton: {
    position: 'absolute',
    right: 6,
    top: 6,
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(20, 28, 36, 0.78)',
  },
  removeButtonPressed: { opacity: 0.65 },
  meta: { color: theme.colors.mutedInk, fontSize: 11 },
});
