import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import { API_BASE_URL } from '@/api/client';
import { generatedFileLabel, type GeneratedFile } from '@/chat/generatedFiles';

const DOWNLOADS_DIRECTORY_URI_KEY = 'generated_files_downloads_directory_uri';
const { StorageAccessFramework } = FileSystem;

type DownloadGeneratedFileResult = {
  fileName: string;
  label: string;
  openUri: string;
  savedToDownloads: boolean;
  fallbackWarning?: string;
};

function safeFileName(file: GeneratedFile): string {
  return (file.filename || `${file.artifact_id || 'generated'}.pdf`).replace(/[\\/:*?"<>|]/g, '_');
}

function resolveDownloadEndpoint(file: GeneratedFile): string | null {
  const path =
    file.mobile_download_url?.trim() ||
    file.download_url?.trim() ||
    (file.artifact_id
      ? `/mobile/generated-pdfs/${encodeURIComponent(file.artifact_id)}/download`
      : '');
  if (!path) return null;
  return path.startsWith('http') ? path : `${API_BASE_URL}${path}`;
}

async function downloadWithAuthRetry(
  endpoint: string,
  destination: string,
  token: string,
  refreshToken: () => Promise<string | null>,
) {
  let activeToken = token;
  let result = await FileSystem.downloadAsync(endpoint, destination, {
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
    result = await FileSystem.downloadAsync(endpoint, destination, {
      headers: {
        Authorization: `Bearer ${activeToken}`,
      },
    });
  }

  if (result.status < 200 || result.status >= 300) {
    throw new Error(`Download failed with status ${result.status}.`);
  }

  return result;
}

function getDownloadedSize(info: FileSystem.FileInfo): number | null {
  return 'size' in info && typeof info.size === 'number' ? info.size : null;
}

async function verifyDownloadedFile(uri: string, expectedSize?: number | null): Promise<void> {
  const info = await FileSystem.getInfoAsync(uri);
  if (!info.exists) {
    throw new Error('Download reported success, but file was not found on device storage.');
  }

  const actualSize = getDownloadedSize(info);
  if (actualSize === 0) {
    await FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
    throw new Error('Download failed: the PDF file was empty.');
  }

  if (actualSize !== null && expectedSize && actualSize < expectedSize) {
    await FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
    throw new Error('Download failed: the PDF file was incomplete.');
  }
}

async function ensureAndroidDownloadsDirectoryUri(forcePrompt: boolean = false): Promise<string> {
  if (!forcePrompt) {
    const cached = await AsyncStorage.getItem(DOWNLOADS_DIRECTORY_URI_KEY);
    if (cached) return cached;
  }

  const initialUri = StorageAccessFramework.getUriForDirectoryInRoot('Download');
  const permission = await StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
  if (!permission.granted || !permission.directoryUri) {
    throw new Error('Downloads access not granted.');
  }

  await AsyncStorage.setItem(DOWNLOADS_DIRECTORY_URI_KEY, permission.directoryUri);
  return permission.directoryUri;
}

async function exportToAndroidDownloads(
  localFileUri: string,
  fileName: string,
  mimeType?: string | null,
): Promise<string> {
  const writeToDownloads = async (directoryUri: string): Promise<string> => {
    const lastDot = fileName.lastIndexOf('.');
    const baseName = lastDot > 0 ? fileName.slice(0, lastDot) : fileName;
    const targetMime = mimeType || 'application/pdf';
    const safFileUri = await StorageAccessFramework.createFileAsync(
      directoryUri,
      baseName,
      targetMime,
    );
    const base64Content = await FileSystem.readAsStringAsync(localFileUri, {
      encoding: FileSystem.EncodingType.Base64,
    });
    if (!base64Content) {
      throw new Error('Download failed: the PDF file was empty.');
    }
    await FileSystem.writeAsStringAsync(safFileUri, base64Content, {
      encoding: FileSystem.EncodingType.Base64,
    });
    return safFileUri;
  };

  const directoryUri = await ensureAndroidDownloadsDirectoryUri(false);
  try {
    return await writeToDownloads(directoryUri);
  } catch {
    await AsyncStorage.removeItem(DOWNLOADS_DIRECTORY_URI_KEY);
    const refreshedDirectoryUri = await ensureAndroidDownloadsDirectoryUri(true);
    return writeToDownloads(refreshedDirectoryUri);
  }
}

export async function downloadGeneratedFile(
  file: GeneratedFile,
  token: string,
  refreshToken: () => Promise<string | null>,
): Promise<DownloadGeneratedFileResult> {
  const endpoint = resolveDownloadEndpoint(file);
  if (!endpoint) {
    throw new Error('Download link unavailable.');
  }

  const documentDirectory = FileSystem.documentDirectory;
  if (!documentDirectory) {
    throw new Error('Download failed: storage unavailable.');
  }

  const fileName = safeFileName(file);
  const destination = `${documentDirectory}${fileName}`;
  const result = await downloadWithAuthRetry(endpoint, destination, token, refreshToken);
  await verifyDownloadedFile(result.uri, file.file_size);

  let openUri = result.uri;
  let savedToDownloads = false;
  let fallbackWarning: string | undefined;

  if (Platform.OS === 'android') {
    try {
      openUri = await exportToAndroidDownloads(result.uri, fileName, file.file_mime);
      savedToDownloads = true;
    } catch (error) {
      fallbackWarning =
        error instanceof Error
          ? error.message
          : 'Could not save to Downloads. File kept in app storage.';
      openUri = await FileSystem.getContentUriAsync(result.uri).catch(() => result.uri);
    }
  }

  return {
    fileName,
    label: generatedFileLabel(file),
    openUri,
    savedToDownloads,
    fallbackWarning,
  };
}
