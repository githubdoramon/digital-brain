import { Platform } from 'react-native';

import RuntimeNative from '@/modules/digital-brain-runtime/src';

export function hasSharedLocationRuntime(): boolean {
  return (
    Platform.OS === 'android' &&
    typeof RuntimeNative?.setRuntimeLocationEnabled === 'function' &&
    typeof RuntimeNative?.configureRuntimeLocationUploader === 'function'
  );
}
