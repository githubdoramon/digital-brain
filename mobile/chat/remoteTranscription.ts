import * as FileSystem from 'expo-file-system/legacy';

import { API_BASE_URL, getAuthRequestContext } from '@/api/client';
import { normalizeTranscriptText } from '@/chat/voiceState';

export type TranscriptionStatus = { stage: 'uploading' | 'transcribing'; progress?: number };
export type TranscriptionSuccess = { text: string };
export const MAX_DICTATION_SECONDS = 600;
const MAX_BYTES = MAX_DICTATION_SECONDS * 32000 + 4096;

export class TranscriptionError extends Error {
  constructor(
    public code: 'cancelled' | 'timeout' | 'auth' | 'too_long' | 'no_speech' | 'failed',
    message: string,
  ) {
    super(message);
  }
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted)
    return Promise.reject(new TranscriptionError('cancelled', 'Dictation cancelled.'));
  return new Promise((resolve, reject) => {
    const abort = () => reject(new TranscriptionError('cancelled', 'Dictation cancelled.'));
    signal.addEventListener('abort', abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

/** One authenticated binary upload. No retries, provider keys, logging or automatic chat send. */
export async function transcribeAudioFile(
  fileUri: string,
  onStatus?: (status: TranscriptionStatus) => void,
  signal?: AbortSignal,
): Promise<TranscriptionSuccess> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 120_000);
  let upload: ReturnType<typeof FileSystem.createUploadTask> | null = null;
  const cancelUpload = () => {
    void upload?.cancelAsync().catch(() => undefined);
  };
  controller.signal.addEventListener('abort', cancelUpload);
  try {
    const info = await abortable(FileSystem.getInfoAsync(fileUri), controller.signal);
    if (!info.exists || info.isDirectory)
      throw new TranscriptionError('failed', 'The recording is unavailable. Please record again.');
    if (info.size > MAX_BYTES)
      throw new TranscriptionError('too_long', 'Record up to ten minutes at a time.');
    const { token } = await abortable(getAuthRequestContext(), controller.signal);
    if (!token) throw new TranscriptionError('auth', 'Sign in again to transcribe your recording.');
    if (controller.signal.aborted)
      throw new TranscriptionError('cancelled', 'Dictation cancelled.');
    onStatus?.({ stage: 'uploading', progress: 0 });
    upload = FileSystem.createUploadTask(
      `${API_BASE_URL.replace(/\/$/u, '')}/mobile/speech/transcribe`,
      fileUri,
      {
        httpMethod: 'POST',
        uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'audio/wav' },
      },
      (event) => {
        if (controller.signal.aborted) return;
        const total = event.totalBytesExpectedToSend;
        const progress = total > 0 ? Math.min(100, (event.totalBytesSent / total) * 100) : 0;
        onStatus?.({ stage: progress >= 100 ? 'transcribing' : 'uploading', progress });
      },
    );
    const response = await abortable(upload.uploadAsync(), controller.signal);
    if (!response || controller.signal.aborted)
      throw new TranscriptionError('cancelled', 'Dictation cancelled.');
    if (response.status === 401 || response.status === 403)
      throw new TranscriptionError('auth', 'Sign in again to transcribe your recording.');
    if (response.status === 413)
      throw new TranscriptionError('too_long', 'Record up to ten minutes at a time.');
    if (response.status < 200 || response.status >= 300 || response.body.length > 40000)
      throw new TranscriptionError(
        'failed',
        'Could not transcribe your recording. Check your connection and try again.',
      );
    const result = JSON.parse(response.body);
    if (
      typeof result.text !== 'string' ||
      result.provider !== 'remote' ||
      result.engine !== 'openai-compatible'
    )
      throw new TranscriptionError(
        'failed',
        'Could not transcribe your recording. Please try again.',
      );
    const text = normalizeTranscriptText(result.text);
    if (!text)
      throw new TranscriptionError('no_speech', 'No speech was detected. Please record again.');
    return { text };
  } catch (error) {
    if (timedOut)
      throw new TranscriptionError('timeout', 'Transcription took too long. Please try again.');
    if (controller.signal.aborted)
      throw new TranscriptionError('cancelled', 'Dictation cancelled.');
    if (error instanceof TranscriptionError) throw error;
    throw new TranscriptionError(
      'failed',
      'Could not transcribe your recording. Check your connection and try again.',
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
    controller.signal.removeEventListener('abort', cancelUpload);
    await FileSystem.deleteAsync(fileUri, { idempotent: true }).catch(() => undefined);
  }
}

export function getTranscriptionErrorMessage(error: unknown) {
  return error instanceof TranscriptionError
    ? error.message
    : 'Unable to transcribe that recording right now.';
}
