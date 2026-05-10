import * as MediaLibrary from 'expo-media-library';
import type * as ImagePicker from 'expo-image-picker';

export type ResolvedPickedImageAsset = {
  assetId?: string | null;
  uri: string;
  displayUri: string;
  fileName: string;
  mimeType: string;
  width?: number | null;
  height?: number | null;
  exif?: Record<string, unknown> | null;
  debug: {
    resolutionMethod: 'media-library-original' | 'picker-fallback';
    pickerUri: string;
    resolvedUri: string;
    pickerFileName?: string | null;
    resolvedFileName: string;
    assetId?: string | null;
    pickerHasGps: boolean;
    resolvedHasGps: boolean;
    pickerExifKeys: string[];
    resolvedExifKeys: string[];
  };
};

function hasGpsExif(exif: Record<string, unknown> | null): boolean {
  if (!exif) {
    return false;
  }
  return [
    exif.GPSLatitude,
    exif.GPSLongitude,
    exif.latitude,
    exif.longitude,
    exif.gpsLatitude,
    exif.gpsLongitude,
  ].some((value) => value !== undefined && value !== null && String(value).trim() !== '');
}

function exifKeys(exif: Record<string, unknown> | null): string[] {
  return exif ? Object.keys(exif).sort() : [];
}

function logResolvedAssetDebug(debug: ResolvedPickedImageAsset['debug']) {
  console.info('[event-photos] resolved picked asset', debug);
}

function inferMimeType(fileName: string, fallback?: string | null): string {
  const normalizedFallback = String(fallback ?? '').trim();
  if (normalizedFallback) {
    return normalizedFallback;
  }

  const lower = fileName.toLowerCase();
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.heic')) return 'image/heic';
  if (lower.endsWith('.heif')) return 'image/heif';
  if (lower.endsWith('.webp')) return 'image/webp';
  return 'image/jpeg';
}

function normalizeExif(exif: unknown): Record<string, unknown> | null {
  return exif && typeof exif === 'object' ? (exif as Record<string, unknown>) : null;
}

export async function resolvePickedImageAsset(
  asset: ImagePicker.ImagePickerAsset,
): Promise<ResolvedPickedImageAsset> {
  const pickerUri = String(asset.uri ?? '').trim();
  if (!pickerUri) {
    throw new Error('Selected photo is missing a URI.');
  }

  const pickerFileName = String(asset.fileName ?? '').trim();
  const pickerExif = normalizeExif(asset.exif);

  if (asset.assetId) {
    try {
      const info = await MediaLibrary.getAssetInfoAsync(asset.assetId);
      const assetLocalUri = String(info.localUri ?? '').trim();
      const assetFileName = String(info.filename ?? '').trim();
      const resolvedUri = assetLocalUri || pickerUri;
      const resolvedFileName = assetFileName || pickerFileName || `photo-${Date.now()}.jpg`;
      const resolved = {
        assetId: asset.assetId,
        uri: resolvedUri,
        displayUri: pickerUri,
        fileName: resolvedFileName,
        mimeType: inferMimeType(resolvedFileName, asset.mimeType),
        width: typeof asset.width === 'number' ? asset.width : null,
        height: typeof asset.height === 'number' ? asset.height : null,
        exif: normalizeExif(info.exif) || pickerExif,
        debug: {
          resolutionMethod: 'media-library-original',
          pickerUri,
          resolvedUri,
          pickerFileName: pickerFileName || null,
          resolvedFileName: resolvedFileName,
          assetId: asset.assetId,
          pickerHasGps: hasGpsExif(pickerExif),
          resolvedHasGps: hasGpsExif(normalizeExif(info.exif) || pickerExif),
          pickerExifKeys: exifKeys(pickerExif),
          resolvedExifKeys: exifKeys(normalizeExif(info.exif) || pickerExif),
        },
      };
      logResolvedAssetDebug(resolved.debug);
      return resolved;
    } catch {
      // Fall back to the picker-exported asset if MediaLibrary cannot resolve the original file.
    }
  }

  const fallbackFileName = pickerFileName || `photo-${Date.now()}.jpg`;
  const fallback = {
    assetId: asset.assetId ?? null,
    uri: pickerUri,
    displayUri: pickerUri,
    fileName: fallbackFileName,
    mimeType: inferMimeType(fallbackFileName, asset.mimeType),
    width: typeof asset.width === 'number' ? asset.width : null,
    height: typeof asset.height === 'number' ? asset.height : null,
    exif: pickerExif,
    debug: {
      resolutionMethod: 'picker-fallback',
      pickerUri,
      resolvedUri: pickerUri,
      pickerFileName: pickerFileName || null,
      resolvedFileName: fallbackFileName,
      assetId: asset.assetId ?? null,
      pickerHasGps: hasGpsExif(pickerExif),
      resolvedHasGps: hasGpsExif(pickerExif),
      pickerExifKeys: exifKeys(pickerExif),
      resolvedExifKeys: exifKeys(pickerExif),
    },
  };
  logResolvedAssetDebug(fallback.debug);
  return fallback;
}
