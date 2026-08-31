import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import DigitalBrainStorageNative from '@/modules/digital-brain-storage/src';

export enum DigitalBrainStorageFolder {
  Recordings = 'Recordings',
  GlassesCaptureQueue = 'Glasses Capture Queue',
  ImagePipelineTemp = 'Image Pipeline Temp',
  Exports = 'Exports',
}

const LEGACY_IMAGE_PIPELINE_FOLDER = 'Smart Glasses POC 2';

const STORAGE_BASE_URI_KEY = 'digitalbrain.storage.base_folder.v1';
let storageCopyChain: Promise<void> = Promise.resolve();

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
  if (!stored) return null;
  return normalizeDigitalBrainStorageUri(stored);
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
  if (folder === DigitalBrainStorageFolder.ImagePipelineTemp) {
    await DigitalBrainStorageNative.renameSubdirectoryIfExists(
      baseUri,
      LEGACY_IMAGE_PIPELINE_FOLDER,
      folder,
    );
  }
  // Preserve the provider-returned child document URI exactly. Rebuilding it
  // as a child tree loses the grant attached to the selected base directory.
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

async function copyToDigitalBrainStorageNow(
  sourceUri: string,
  folder: DigitalBrainStorageFolder,
  fileName: string,
  mimeType: string,
  skipIfSameSize: boolean,
): Promise<string> {
  const sourceInfo = await FileSystem.getInfoAsync(sourceUri);
  if (!sourceInfo.exists || !('size' in sourceInfo) || !sourceInfo.size) {
    throw new Error('Digital Brain cannot copy a missing or empty source file.');
  }
  const baseUri = await getDigitalBrainStorageBaseUri();
  if (!baseUri) {
    throw new Error('Choose a Digital Brain storage location before saving files.');
  }
  if (!DigitalBrainStorageNative) {
    throw new Error('Digital Brain storage needs an Android rebuild before it can save files.');
  }
  if (folder === DigitalBrainStorageFolder.ImagePipelineTemp) {
    await DigitalBrainStorageNative.renameSubdirectoryIfExists(
      baseUri,
      LEGACY_IMAGE_PIPELINE_FOLDER,
      folder,
    );
  }
  const name = safeStorageFileName(fileName, 'digital-brain-file');
  const result = await DigitalBrainStorageNative.copyToSubdirectory(
    baseUri,
    folder,
    sourceUri,
    name,
    mimeType,
    skipIfSameSize,
  );
  if (result.bytes !== sourceInfo.size) {
    throw new Error(
      `Digital Brain could not verify the saved file (${result.bytes}/${sourceInfo.size} bytes).`,
    );
  }
  return result.uri;
}

export function copyToDigitalBrainStorage(
  sourceUri: string,
  folder: DigitalBrainStorageFolder,
  fileName: string,
  mimeType: string,
  options: { skipIfSameSize?: boolean } = {},
): Promise<string> {
  // Android document-provider writes are serialized. Concurrent writes to the
  // same granted tree can race while replacing a file and expose partial data.
  const copy = storageCopyChain
    .catch(() => undefined)
    .then(() =>
      copyToDigitalBrainStorageNow(
        sourceUri,
        folder,
        fileName,
        mimeType,
        options.skipIfSameSize === true,
      ),
    );
  storageCopyChain = copy.then(
    () => undefined,
    () => undefined,
  );
  return copy;
}
