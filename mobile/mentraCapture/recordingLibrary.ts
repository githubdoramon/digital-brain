import AsyncStorage from '@react-native-async-storage/async-storage';

export type GlassesAudioRecording = {
  id: string;
  uri: string;
  name: string;
  startedAt: string;
  durationMs: number;
  sizeBytes: number;
};

const STORAGE_KEY = 'digitalbrain.mentra.audio.recordings.v1';
let cached: GlassesAudioRecording[] | null = null;
let loading: Promise<GlassesAudioRecording[]> | null = null;
let writes: Promise<void> = Promise.resolve();

// Listing uses the durable index. Stat-ing every SAF document on each screen
// focus queues provider work ahead of microphone controls and scales with the
// entire library. Validate the specific file when the user opens it instead.
export async function readRecordingLibrary(): Promise<GlassesAudioRecording[]> {
  if (cached) return cached;
  if (!loading) {
    loading = AsyncStorage.getItem(STORAGE_KEY)
      .then((raw) => {
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(parsed)) throw new Error('The recording library could not be read.');
        cached = (parsed as GlassesAudioRecording[]).sort((a, b) =>
          b.startedAt.localeCompare(a.startedAt),
        );
        return cached;
      })
      .finally(() => {
        loading = null;
      });
  }
  return loading;
}

// Every read-modify-write shares this queue so saving an old recording cannot
// undo a concurrent rename, delete, or newly completed recording.
export function updateRecordingLibrary(
  update: (current: GlassesAudioRecording[]) => GlassesAudioRecording[],
): Promise<void> {
  const write = writes.then(async () => {
    const next = update(await readRecordingLibrary()).sort((a, b) =>
      b.startedAt.localeCompare(a.startedAt),
    );
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    cached = next;
  });
  writes = write.catch(() => undefined);
  return write;
}
