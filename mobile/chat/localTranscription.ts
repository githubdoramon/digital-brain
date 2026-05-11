import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';
import { initWhisper, type WhisperContext } from 'whisper.rn';

import { appendVoiceTranscriptionDebugLog } from '@/debug/voiceTranscriptionDebug';
import { normalizeTranscriptText } from '@/chat/voiceState';

export type LocalTranscriptionStage =
  | 'downloading_model'
  | 'loading_model'
  | 'transcribing';

export type LocalTranscriptionStatus = {
  stage: LocalTranscriptionStage;
  progress?: number;
};

export type LocalTranscriptionSuccess = {
  text: string;
  rawText: string;
  language: string;
  isAborted: boolean;
  segments: {
    text: string;
    t0: number;
    t1: number;
  }[];
};

export type LocalTranscriptionErrorCode =
  | 'unsupported_platform'
  | 'no_speech'
  | 'download_failed'
  | 'transcription_failed';

export class LocalTranscriptionError extends Error {
  code: LocalTranscriptionErrorCode;

  constructor(code: LocalTranscriptionErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

const MODEL_FILE_NAME = 'ggml-tiny.en.bin';
const MODEL_URL = `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_FILE_NAME}`;
const MODEL_DIRECTORY = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}whisper/`;
const MODEL_FILE_URI = `${MODEL_DIRECTORY}${MODEL_FILE_NAME}`;

let whisperContextPromise: Promise<WhisperContext> | null = null;

async function ensureModelFile(onStatus?: (status: LocalTranscriptionStatus) => void) {
  const info = await FileSystem.getInfoAsync(MODEL_FILE_URI);
  if (info.exists && !info.isDirectory) {
    return MODEL_FILE_URI;
  }

  await FileSystem.makeDirectoryAsync(MODEL_DIRECTORY, { intermediates: true });
  onStatus?.({ stage: 'downloading_model', progress: 0 });

  try {
    const result = await FileSystem.createDownloadResumable(
      MODEL_URL,
      MODEL_FILE_URI,
      {},
      (progressEvent) => {
        if (!progressEvent.totalBytesExpectedToWrite) {
          return;
        }

        onStatus?.({
          stage: 'downloading_model',
          progress:
            (progressEvent.totalBytesWritten / progressEvent.totalBytesExpectedToWrite) * 100,
        });
      },
    ).downloadAsync();

    if (!result?.uri) {
      throw new Error('The Whisper model download did not complete.');
    }

    return result.uri;
  } catch (error) {
    throw new LocalTranscriptionError(
      'download_failed',
      error instanceof Error
        ? error.message
        : 'The Whisper model could not be downloaded.',
    );
  }
}

async function getWhisperContext(onStatus?: (status: LocalTranscriptionStatus) => void) {
  if (Platform.OS === 'web') {
    throw new LocalTranscriptionError(
      'unsupported_platform',
      'Voice transcription is available only in native mobile builds.',
    );
  }

  if (!whisperContextPromise) {
    whisperContextPromise = (async () => {
      const modelFileUri = await ensureModelFile(onStatus);
      onStatus?.({ stage: 'loading_model' });
      return initWhisper({
        filePath: modelFileUri,
        useGpu: Platform.OS === 'ios',
      });
    })().catch((error) => {
      whisperContextPromise = null;
      throw error;
    });
  }

  return whisperContextPromise;
}

export async function transcribeAudioFile(
  fileUri: string,
  onStatus?: (status: LocalTranscriptionStatus) => void,
): Promise<LocalTranscriptionSuccess> {
  const whisperContext = await getWhisperContext(onStatus);
  onStatus?.({ stage: 'transcribing', progress: 0 });

  try {
    const { promise } = whisperContext.transcribe(fileUri, {
      language: 'en',
      maxThreads: 4,
      onProgress: (progress: number) => {
        onStatus?.({ stage: 'transcribing', progress });
      },
    });
    const result = await promise;
    const text = normalizeTranscriptText(result.result);

    await appendVoiceTranscriptionDebugLog('voice_transcription_result', {
      fileUri,
      modelFileName: MODEL_FILE_NAME,
      rawText: result.result,
      normalizedText: text,
      language: result.language,
      isAborted: result.isAborted,
      segmentCount: result.segments.length,
      segments: result.segments,
    }).catch(() => undefined);

    if (!text) {
      throw new LocalTranscriptionError(
        'no_speech',
        'No speech was detected in that recording.',
      );
    }

    return {
      text,
      rawText: result.result,
      language: result.language,
      isAborted: result.isAborted,
      segments: result.segments,
    };
  } catch (error) {
    await appendVoiceTranscriptionDebugLog('voice_transcription_failure', {
      fileUri,
      modelFileName: MODEL_FILE_NAME,
      error: error instanceof Error ? error.message : String(error),
    }).catch(() => undefined);

    if (error instanceof LocalTranscriptionError) {
      throw error;
    }

    throw new LocalTranscriptionError(
      'transcription_failed',
      error instanceof Error
        ? error.message
        : 'The on-device transcription failed before a result was returned.',
    );
  }
}

export function getLocalTranscriptionErrorMessage(error: unknown) {
  if (error instanceof LocalTranscriptionError) {
    if (error.code === 'download_failed') {
      return 'The voice model could not be downloaded. Connect to the internet and try again.';
    }

    return error.message;
  }

  return error instanceof Error
    ? error.message
    : 'Unable to transcribe that recording right now.';
}
