import { NativeModule, requireOptionalNativeModule } from 'expo';

export type AppRuntimeStatus = {
  active: boolean;
  owners: string[];
  locationActive: boolean;
  startedAtMs: number | null;
  nativeUploadRunCount: number;
  nativeUploadSampleCount: number;
  nativeUploadLastDurationMs: number;
  nativeUploadLastOutcome: string;
  nativeUploadLastHttpStatus: number | null;
  nativeUploadLastQueueSize: number;
  nativeLocationQueueSize: number;
  lastError: string | null;
  foregroundTypes: number;
};

class DigitalBrainRuntimeModule extends NativeModule {
  setRuntimeLocationEnabled(_enabled: boolean): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  configureRuntimeLocationUploader(_apiBaseUrl: string, _googleWebClientId: string): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  getAppRuntimeStatus(): Promise<AppRuntimeStatus> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  getRuntimeEnergyDiagnostics(): Promise<Record<string, unknown>> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
}

export default requireOptionalNativeModule<DigitalBrainRuntimeModule>('DigitalBrainRuntime');
