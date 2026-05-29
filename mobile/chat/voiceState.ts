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
  const normalized = text.replace(/\s+/gu, ' ').trim();
  return normalizeSpokenSlashCommand(normalized);
}

function normalizeSpokenSlashCommand(text: string) {
  return text.replace(/^[sS]lash[\s,.:;!?-]+([a-z][a-z0-9-]*)(?=\b|[\s,.:;!?-]|$)/iu, '/$1');
}

export function formatVoiceDuration(durationMillis: number) {
  const totalSeconds = Math.max(0, Math.round(durationMillis / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}
