import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import DigitalBrainStorageNative from '@/modules/digital-brain-storage/src';

export enum DigitalBrainStorageFolder {
  Recordings = 'Recordings',
  GlassesCaptureQueue = 'Glasses Capture Queue',
  Exports = 'Exports',
}

const STORAGE_BASE_URI_KEY = 'digitalbrain.storage.base_folder.v1';

export function normalizeDigitalBrainStorageUri(uri: string): string {
  return uri.trim().replace(/[?#].*$/, '');
}

export function digitalBrainStorageFolderLabel(uri: string): string {
  const normalized = normalizeDigitalBrainStorageUri(uri);
  const encodedTreeId = normalized.match(/\/tree\/([^/]+)/)?.[1];
  const treeId = encodedTreeId ? decodeURIComponent(encodedTreeId) : '';
  const path = treeId.includes(':') ? treeId.slice(treeId.indexOf(':') + 1) : treeId;
  const segments = path.split('/').filter(Boolean);
  return segments.join(' / ') || 'Selected folder';
}

export async function getDigitalBrainStorageBaseUri(): Promise<string | null> {
  const stored = await AsyncStorage.getItem(STORAGE_BASE_URI_KEY);
  return stored ? normalizeDigitalBrainStorageUri(stored) : null;
}

export async function setDigitalBrainStorageBaseUri(uri: string): Promise<void> {
  await AsyncStorage.setItem(STORAGE_BASE_URI_KEY, normalizeDigitalBrainStorageUri(uri));
}

export async function clearDigitalBrainStorageBaseUri(): Promise<void> {
  await AsyncStorage.removeItem(STORAGE_BASE_URI_KEY);
}

export async function chooseDigitalBrainStorageBaseUri(): Promise<string | null> {
  if (Platform.OS !== 'android') {
    throw new Error('The shared Digital Brain folder is currently available on Android only.');
  }
  const initialUri = FileSystem.StorageAccessFramework.getUriForDirectoryInRoot('Documents');
  const result =
    await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync(initialUri);
  if (!result.granted || !result.directoryUri) return null;
  const uri = normalizeDigitalBrainStorageUri(result.directoryUri);
  await setDigitalBrainStorageBaseUri(uri);
  return uri;
}

export async function getDigitalBrainStorageFolder(
  folder: DigitalBrainStorageFolder,
): Promise<string | null> {
  const baseUri = await getDigitalBrainStorageBaseUri();
  if (!baseUri) return null;
  if (!DigitalBrainStorageNative) {
    throw new Error('Digital Brain storage needs an Android rebuild before it can create folders.');
  }
  return (await DigitalBrainStorageNative.ensureSubdirectory(baseUri, folder)).uri;
}

export function safeStorageFileName(name: string, fallback: string): string {
  const cleaned = name
    .trim()
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .slice(0, 120);
  return cleaned || fallback;
}

export async function copyToDigitalBrainStorage(
  sourceUri: string,
  folder: DigitalBrainStorageFolder,
  fileName: string,
  mimeType: string,
): Promise<string> {
  const destinationFolder = await getDigitalBrainStorageFolder(folder);
  if (!destinationFolder) {
    throw new Error('Choose a Digital Brain storage location before saving files.');
  }
  const name = safeStorageFileName(fileName, 'digital-brain-file');
  const existing = await FileSystem.StorageAccessFramework.readDirectoryAsync(
    destinationFolder,
  ).catch(() => []);
  const existingUri = existing.find((uri) => decodeURIComponent(uri).endsWith(`/${name}`));
  if (existingUri)
    await FileSystem.deleteAsync(existingUri, { idempotent: true }).catch(() => undefined);
  const destination = await FileSystem.StorageAccessFramework.createFileAsync(
    destinationFolder,
    name,
    mimeType,
  );
  await FileSystem.copyAsync({ from: sourceUri, to: destination });
  const info = await FileSystem.getInfoAsync(destination);
  if (!info.exists || !('size' in info) || !info.size) {
    await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
    throw new Error('Digital Brain could not verify the saved file.');
  }
  return destination;
}
