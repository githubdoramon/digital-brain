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
};

export default requireOptionalNativeModule<DigitalBrainStorageNativeModule>('DigitalBrainStorage');
