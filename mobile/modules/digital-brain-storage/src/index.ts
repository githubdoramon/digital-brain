import { requireOptionalNativeModule } from 'expo';

type DigitalBrainStorageNativeModule = {
  ensureSubdirectory(baseUri: string, name: string): Promise<{ uri: string; name: string }>;
  renameDocument(uri: string, name: string): Promise<{ uri: string; name: string }>;
};

export default requireOptionalNativeModule<DigitalBrainStorageNativeModule>('DigitalBrainStorage');
