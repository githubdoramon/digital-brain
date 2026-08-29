export type CaptureKind = 'photo' | 'video';

export type CaptureState =
  | 'discovered'
  | 'downloading'
  | 'local_ready'
  | 'glasses_acked'
  | 'uploading'
  | 'uploaded'
  | 'failed'
  | 'missing';

export type RemoteCapture = {
  captureId: string;
  kind: CaptureKind;
  capturedAt: string | null;
  fileName: string;
  downloadUrl: string;
  mimeType: string;
  sizeBytes: number | null;
  ackId: string | null;
  protocolVersion: number;
};

export type CaptureQueueEntry = RemoteCapture & {
  state: CaptureState;
  localUri: string | null;
  attempts: number;
  nextRetryAt: string | null;
  lastError: string | null;
  discoveredAt: string;
  updatedAt: string;
  immichAssetId: string | null;
  /** The glasses was already acknowledged, so this file can upload without BLE. */
  uploadReady?: boolean;
  location?: CaptureLocation | null;
};

/** A phone location sample selected for the capture timestamp. */
export type CaptureLocation = {
  lat: number;
  lon: number;
  accuracy_m?: number;
  captured_at: string;
  source: string;
  provenance: 'phone_location_history';
  sample_captured_at: string;
  sample_source?: string;
  offset_ms: number;
  tolerance_ms: number;
};

export type CaptureSyncStatus = {
  running: boolean;
  lastRunAt: string | null;
  lastError: string | null;
  pendingCount: number;
  failedCount: number;
  uploadedCount: number;
  currentCaptureId: string | null;
  networkPath: 'current_wifi' | 'glasses_hotspot' | 'unavailable' | null;
};
