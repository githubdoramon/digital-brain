import * as FileSystem from 'expo-file-system/legacy';
import type * as ImagePicker from 'expo-image-picker';

export const MAX_CHAT_MEDIA_ATTACHMENTS = 5;
export const MAX_CHAT_MEDIA_BYTES = 6 * 1024 * 1024;

export type ComposerMediaAttachment = {
  attachmentId: string;
  fileName: string;
  mimeType: string;
  uri: string;
  contentBase64: string;
  source: 'mobile_chat';
  localAssetId?: string | null;
  capturedAt?: string | null;
  width?: number | null;
  height?: number | null;
};

export type ChatMediaAttachmentPayload = {
  attachment_id: string;
  file_name: string;
  mime_type: string;
  content_base64: string;
  source: string;
  local_asset_id?: string | null;
  captured_at?: string | null;
  width?: number | null;
  height?: number | null;
};

function exifDateToIso(value: unknown): string | null {
  const raw = String(value ?? '').trim();
  if (!raw) {
    return null;
  }

  const exifMatch = raw.match(
    /^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:([+-]\d{2}:?\d{2}|Z))?$/,
  );
  if (exifMatch) {
    const [, year, month, day, hour, minute, second, suffix] = exifMatch;
    const timezone = suffix ? (suffix.includes(':') || suffix === 'Z' ? suffix : `${suffix.slice(0, 3)}:${suffix.slice(3)}`) : 'Z';
    return `${year}-${month}-${day}T${hour}:${minute}:${second}${timezone}`;
  }

  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toISOString();
}

export function extractAssetCapturedAt(asset: ImagePicker.ImagePickerAsset): string | null {
  const exif = asset.exif && typeof asset.exif === 'object' ? asset.exif : null;
  if (!exif) {
    return null;
  }

  return (
    exifDateToIso(exif.DateTimeOriginal) ||
    exifDateToIso(exif.DateTimeDigitized) ||
    exifDateToIso(exif.DateTime) ||
    null
  );
}

export async function buildComposerMediaAttachment(
  asset: ImagePicker.ImagePickerAsset,
): Promise<ComposerMediaAttachment> {
  if (!asset.uri) {
    throw new Error('Selected photo is missing a URI.');
  }

  const fileInfo = await FileSystem.getInfoAsync(asset.uri);
  const fileSize = fileInfo.exists && typeof fileInfo.size === 'number' ? fileInfo.size : null;
  if (fileSize !== null && fileSize > MAX_CHAT_MEDIA_BYTES) {
    const maxMb = Math.round((MAX_CHAT_MEDIA_BYTES / (1024 * 1024)) * 10) / 10;
    throw new Error(`Each attached photo must be ${maxMb} MB or smaller.`);
  }

  const contentBase64 = await FileSystem.readAsStringAsync(asset.uri, {
    encoding: FileSystem.EncodingType.Base64,
  });
  if (!contentBase64.trim()) {
    throw new Error('Unable to read that photo right now.');
  }

  return {
    attachmentId: `chat-media-${Date.now()}-${Math.round(Math.random() * 1_000_000)}`,
    fileName: asset.fileName || `chat-photo-${Date.now()}.jpg`,
    mimeType: asset.mimeType || 'image/jpeg',
    uri: asset.uri,
    contentBase64,
    source: 'mobile_chat',
    localAssetId: asset.assetId ?? null,
    capturedAt: extractAssetCapturedAt(asset),
    width: asset.width ?? null,
    height: asset.height ?? null,
  };
}

export function toChatMediaAttachmentPayload(
  attachment: ComposerMediaAttachment,
): ChatMediaAttachmentPayload {
  return {
    attachment_id: attachment.attachmentId,
    file_name: attachment.fileName,
    mime_type: attachment.mimeType,
    content_base64: attachment.contentBase64,
    source: attachment.source,
    local_asset_id: attachment.localAssetId ?? null,
    captured_at: attachment.capturedAt ?? null,
    width: attachment.width ?? null,
    height: attachment.height ?? null,
  };
}
