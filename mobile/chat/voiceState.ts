export type VoiceComposerPhase = 'idle' | 'starting' | 'recording' | 'locked' | 'transcribing';

export function mergeTranscriptIntoDraft(currentDraft: string, transcript: string) {
  const normalizedTranscript = normalizeTranscriptText(transcript);
  const normalizedDraft = currentDraft.trim();

  if (!normalizedTranscript) {
    return currentDraft;
  }

  if (!normalizedDraft) {
    return normalizedTranscript;
  }

  return `${currentDraft.replace(/\s+$/u, '')} ${normalizedTranscript}`;
}

export function normalizeTranscriptText(text: string) {
  return text.replace(/\s+/gu, ' ').trim();
}

export function formatVoiceDuration(durationMillis: number) {
  const totalSeconds = Math.max(0, Math.round(durationMillis / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}
