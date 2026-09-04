import { requireOptionalNativeModule } from 'expo';

type DigitalBrainStorageNativeModule = {
  ensureSubdirectory(baseUri: string, name: string): Promise<{ uri: string; name: string }>;
  renameSubdirectoryIfExists(
    baseUri: string,
    oldName: string,
    newName: string,
  ): Promise<{ renamed: boolean; uri: string | null }>;
  renameDocument(uri: string, name: string): Promise<{ uri: string; name: string }>;
  copyToSubdirectory(
    baseUri: string,
    folder: string,
    sourceUri: string,
    name: string,
    mimeType: string,
    skipIfSameSize: boolean,
  ): Promise<{ uri: string; name: string; bytes: number; reused: boolean }>;
  uploadFileRange(
    url: string,
    sourceUri: string,
    offset: number,
    length: number,
    headers: Record<string, string>,
  ): Promise<{ status: number; body: string }>;
  listSubdirectory(
    baseUri: string,
    folder: string,
  ): Promise<{ uri: string; name: string; mimeType: string; bytes: number }[]>;
  getFileInfo(uri: string): Promise<{ exists: boolean; bytes: number }>;
  deleteFile(uri: string): Promise<void>;
};

export default requireOptionalNativeModule<DigitalBrainStorageNativeModule>('DigitalBrainStorage');
