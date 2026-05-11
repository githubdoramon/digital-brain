import * as FileSystem from 'expo-file-system/legacy';

const VOICE_DEBUG_DIRECTORY = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}voice-debug/`;
const VOICE_DEBUG_LOG_FILE_NAME = 'voice-transcription-debug-log.txt';
const VOICE_DEBUG_LOG_URI = `${VOICE_DEBUG_DIRECTORY}${VOICE_DEBUG_LOG_FILE_NAME}`;
const VOICE_DEBUG_AUDIO_FILE_NAME = 'latest-voice-input.m4a';
const VOICE_DEBUG_AUDIO_URI = `${VOICE_DEBUG_DIRECTORY}${VOICE_DEBUG_AUDIO_FILE_NAME}`;

function serializePayload(payload: unknown): string {
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

async function ensureVoiceDebugDirectory() {
  await FileSystem.makeDirectoryAsync(VOICE_DEBUG_DIRECTORY, { intermediates: true });
}

export async function appendVoiceTranscriptionDebugLog(label: string, payload: unknown): Promise<void> {
  await ensureVoiceDebugDirectory();
  const timestamp = new Date().toISOString();
  const nextEntry = [`[${timestamp}] ${label}`, serializePayload(payload), '', ''].join('\n');
  await FileSystem.writeAsStringAsync(VOICE_DEBUG_LOG_URI, nextEntry, {
    encoding: FileSystem.EncodingType.UTF8,
    append: true,
  });
}

export async function copyLatestVoiceDebugAudio(sourceUri: string): Promise<{ uri: string; sizeBytes: number }> {
  await ensureVoiceDebugDirectory();
  await FileSystem.deleteAsync(VOICE_DEBUG_AUDIO_URI, { idempotent: true }).catch(() => undefined);
  await FileSystem.copyAsync({ from: sourceUri, to: VOICE_DEBUG_AUDIO_URI });
  const info = await FileSystem.getInfoAsync(VOICE_DEBUG_AUDIO_URI);
  return {
    uri: VOICE_DEBUG_AUDIO_URI,
    sizeBytes: info.exists && typeof info.size === 'number' ? info.size : 0,
  };
}

export async function getVoiceTranscriptionDebugInfo(): Promise<{
  logExists: boolean;
  logSizeBytes: number;
  audioExists: boolean;
  audioSizeBytes: number;
  audioUri: string | null;
}> {
  const [logInfo, audioInfo] = await Promise.all([
    FileSystem.getInfoAsync(VOICE_DEBUG_LOG_URI),
    FileSystem.getInfoAsync(VOICE_DEBUG_AUDIO_URI),
  ]);

  return {
    logExists: logInfo.exists,
    logSizeBytes: logInfo.exists && typeof logInfo.size === 'number' ? logInfo.size : 0,
    audioExists: audioInfo.exists,
    audioSizeBytes: audioInfo.exists && typeof audioInfo.size === 'number' ? audioInfo.size : 0,
    audioUri: audioInfo.exists ? VOICE_DEBUG_AUDIO_URI : null,
  };
}

export async function readVoiceTranscriptionDebugLog(): Promise<string> {
  const info = await FileSystem.getInfoAsync(VOICE_DEBUG_LOG_URI);
  if (!info.exists) {
    return '';
  }

  return FileSystem.readAsStringAsync(VOICE_DEBUG_LOG_URI, {
    encoding: FileSystem.EncodingType.UTF8,
  });
}

export async function clearVoiceTranscriptionDebugArtifacts(): Promise<void> {
  await Promise.all([
    FileSystem.deleteAsync(VOICE_DEBUG_LOG_URI, { idempotent: true }).catch(() => undefined),
    FileSystem.deleteAsync(VOICE_DEBUG_AUDIO_URI, { idempotent: true }).catch(() => undefined),
  ]);
}

export function getVoiceTranscriptionDebugLogFileName(timestamp = new Date()): string {
  return `digital-brain-voice-debug-${timestamp.toISOString().replace(/[:.]/g, '-')}.txt`;
}

export function getVoiceTranscriptionDebugAudioFileName(timestamp = new Date()): string {
  return `digital-brain-voice-sample-${timestamp.toISOString().replace(/[:.]/g, '-')}.m4a`;
}
