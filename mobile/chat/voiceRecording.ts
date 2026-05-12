import { AudioStudioModule } from '@siteed/audio-studio';
import { Platform } from 'react-native';

export type VoiceRecordingErrorCode =
  | 'permission_denied'
  | 'unsupported_platform'
  | 'missing_recording'
  | 'recording_failed';

export class VoiceRecordingError extends Error {
  code: VoiceRecordingErrorCode;

  constructor(code: VoiceRecordingErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

export async function ensureVoiceRecordingReady() {
  if (Platform.OS === 'web') {
    throw new VoiceRecordingError(
      'unsupported_platform',
      'Voice dictation is available only in native mobile builds.',
    );
  }

  const currentPermission = await AudioStudioModule.getPermissionsAsync?.();
  const granted = currentPermission?.granted
    ? true
    : (await AudioStudioModule.requestPermissionsAsync?.())?.granted;

  if (!granted) {
    throw new VoiceRecordingError(
      'permission_denied',
      'Microphone access is required to dictate a message.',
    );
  }
}

export async function restoreVoiceAudioMode() {
  return undefined;
}

export function requireRecordingUri(recordingUri: string | null | undefined) {
  if (!recordingUri) {
    throw new VoiceRecordingError(
      'missing_recording',
      'The recording finished without an audio file to transcribe.',
    );
  }

  return recordingUri;
}

export function getVoiceRecordingErrorMessage(error: unknown) {
  if (error instanceof VoiceRecordingError) {
    return error.message;
  }

  return error instanceof Error
    ? error.message
    : 'Unable to record voice input right now.';
}
