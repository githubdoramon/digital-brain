import { NativeModule, requireOptionalNativeModule } from 'expo';

export type AppRuntimeStatus = {
  active: boolean;
  owners: string[];
  locationActive: boolean;
  startedAtMs: number | null;
  lastNativeTickAtMs: number | null;
  nativeTickCount: number;
  workRequestCount: number;
  lastWorkDurationMs: number;
  lastError: string | null;
  foregroundTypes: number;
};

export type RuntimeLocationSample = {
  id: string;
  latitude: number;
  longitude: number;
  timestamp: number;
  accuracy: number | null;
  timezone: string;
};

class DigitalBrainRuntimeModule extends NativeModule {
  setRuntimeLocationEnabled(_enabled: boolean): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  getAppRuntimeStatus(): Promise<AppRuntimeStatus> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  getRuntimeEnergyDiagnostics(): Promise<Record<string, unknown>> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  completeRuntimeWork(_workToken: string): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  readRuntimeLocations(): Promise<RuntimeLocationSample[]> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
  acknowledgeRuntimeLocations(_ids: string[]): Promise<void> {
    throw new Error('Native DigitalBrainRuntime implementation is unavailable');
  }
}

export default requireOptionalNativeModule<DigitalBrainRuntimeModule>('DigitalBrainRuntime');
