import Ionicons from '@expo/vector-icons/Ionicons';
import React from 'react';
import { Image, ScrollView, StyleSheet, Text, View } from 'react-native';

import { API_BASE_URL } from '@/api/client';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { type EventPhoto } from '@/components/event-draft/types';
import { theme } from '@/theme';

type EventPhotoCardProps = {
  photos: EventPhoto[];
  editable: boolean;
  isUploading: boolean;
  token?: string | null;
  onAddPhoto?: () => void;
  onRemovePhoto?: (assetId: string) => void;
};

function formatPhotoMeta(photo: EventPhoto) {
  const capturedAt = String(photo.captured_at || '').trim();
  if (!capturedAt) return 'Synced from Immich';
  const parsed = new Date(capturedAt);
  if (Number.isNaN(parsed.getTime())) return 'Synced from Immich';
  return parsed.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export function EventPhotoCard({
  photos,
  editable,
  isUploading,
  token,
  onAddPhoto,
  onRemovePhoto,
}: EventPhotoCardProps) {
  const headers = React.useMemo(
    () => (token ? { Authorization: `Bearer ${token}` } : undefined),
    [token],
  );

  return (
    <>
      <View style={styles.headerRow}>
        <View style={styles.headerCopy}>
          <Text style={styles.label}>Photos</Text>
          <Text style={styles.helperText}>Stored in Immich and linked back to this event.</Text>
        </View>
        {editable && onAddPhoto ? (
          <Button
            label={isUploading ? 'Uploading...' : 'Add photo'}
            variant="secondary"
            onPress={onAddPhoto}
            disabled={isUploading}
          />
        ) : null}
      </View>

      {photos.length === 0 ? (
        <Text style={styles.emptyText}>No photos linked yet.</Text>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.photoRow}>
          {photos.map((photo) => {
            const taggedContacts = photo.tagged_contacts || [];
            const thumbnailUri = photo.thumbnail_path
              ? `${API_BASE_URL}${photo.thumbnail_path}`
              : null;
            return (
              <View key={photo.asset_id} style={styles.photoCard}>
                {thumbnailUri ? (
                  <Image
                    source={{ uri: thumbnailUri, headers }}
                    style={styles.photoImage}
                    resizeMode="cover"
                  />
                ) : (
                  <View style={[styles.photoImage, styles.photoFallback]}>
                    <Ionicons name="image-outline" size={24} color={theme.colors.mutedInk} />
                  </View>
                )}
                <Text style={styles.photoMeta}>{formatPhotoMeta(photo)}</Text>
                {taggedContacts.length > 0 ? (
                  <View style={styles.tagRow}>
                    {taggedContacts.slice(0, 3).map((contact) => (
                      <View key={`${photo.asset_id}:${contact.contact_id}`} style={styles.tagChip}>
                        <Text style={styles.tagChipText}>{contact.display_name}</Text>
                      </View>
                    ))}
                    {taggedContacts.length > 3 ? (
                      <View style={styles.tagChip}>
                        <Text style={styles.tagChipText}>{`+${taggedContacts.length - 3}`}</Text>
                      </View>
                    ) : null}
                  </View>
                ) : (
                  <Text style={styles.emptyTags}>No recognized people linked.</Text>
                )}
                {editable && onRemovePhoto ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Unlink photo"
                    onPress={() => onRemovePhoto(photo.asset_id)}
                    style={({ pressed }) => [styles.unlinkButton, pressed && styles.unlinkButtonPressed]}
                  >
                    <Text style={styles.unlinkText}>Unlink</Text>
                  </Pressable>
                ) : null}
              </View>
            );
          })}
        </ScrollView>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
  },
  headerCopy: {
    flex: 1,
    gap: 4,
  },
  label: {
    color: theme.colors.ink,
    fontSize: 13,
    fontWeight: '600',
  },
  helperText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
    lineHeight: 17,
  },
  emptyText: {
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  photoRow: {
    gap: 12,
    paddingRight: 4,
  },
  photoCard: {
    width: 180,
    gap: 8,
  },
  photoImage: {
    width: 180,
    height: 132,
    borderRadius: theme.radius.lg,
    backgroundColor: '#fff',
  },
  photoFallback: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  photoMeta: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  tagRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  tagChip: {
    borderRadius: 999,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: theme.colors.line,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  tagChipText: {
    color: theme.colors.ink,
    fontSize: 11,
    fontWeight: '600',
  },
  emptyTags: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  unlinkButton: {
    alignSelf: 'flex-start',
    minHeight: 30,
    justifyContent: 'center',
  },
  unlinkButtonPressed: {
    opacity: 0.72,
  },
  unlinkText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.accentDeep,
  },
});
