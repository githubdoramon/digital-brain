import { Buffer } from 'buffer';
import * as FileSystem from 'expo-file-system/legacy';

const SAMPLE_RATE = 16_000;
const MAX_RETAINED_COMMAND_AUDIO_FILES = 40;
const AUDIO_DIRECTORY = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory ?? ''}Digital Brain/Wake Command Debug/`;

export type RetainedWakeCommandAudio = {
  fileName: string;
  uri: string;
  sizeBytes: number;
};

function safeFileToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]/g, '_').slice(-48);
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function pcm16ToWav(pcm: ArrayBuffer): Uint8Array {
  const pcmBytes = new Uint8Array(pcm);
  const wav = new Uint8Array(44 + pcmBytes.length);
  const view = new DataView(wav.buffer);
  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + pcmBytes.length, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, 'data');
  view.setUint32(40, pcmBytes.length, true);
  wav.set(pcmBytes, 44);
  return wav;
}

async function pruneRetainedWakeCommandAudio(): Promise<void> {
  const names = (await FileSystem.readDirectoryAsync(AUDIO_DIRECTORY).catch(() => []))
    .filter((name) => name.endsWith('.wav'))
    .sort();
  const staleNames = names.slice(0, Math.max(0, names.length - MAX_RETAINED_COMMAND_AUDIO_FILES));
  await Promise.all(
    staleNames.map((name) =>
      FileSystem.deleteAsync(`${AUDIO_DIRECTORY}${name}`, { idempotent: true }),
    ),
  );
}

export async function retainWakeCommandAudio(
  commandId: string,
  wakeDetectedAt: number,
  pcm: ArrayBuffer,
): Promise<RetainedWakeCommandAudio> {
  await FileSystem.makeDirectoryAsync(AUDIO_DIRECTORY, { intermediates: true });
  const timestamp = new Date(wakeDetectedAt).toISOString().replace(/[:.]/g, '-');
  const fileName = `wake-command-${timestamp}-${safeFileToken(commandId)}.wav`;
  const uri = `${AUDIO_DIRECTORY}${fileName}`;
  const wav = pcm16ToWav(pcm);
  await FileSystem.writeAsStringAsync(uri, Buffer.from(wav).toString('base64'), {
    encoding: FileSystem.EncodingType.Base64,
  });
  await pruneRetainedWakeCommandAudio();
  return { fileName, uri, sizeBytes: wav.byteLength };
}

export async function getRetainedWakeCommandAudio(): Promise<RetainedWakeCommandAudio[]> {
  const names = (await FileSystem.readDirectoryAsync(AUDIO_DIRECTORY).catch(() => []))
    .filter((name) => name.endsWith('.wav'))
    .sort();
  const retained = await Promise.all(
    names.map(async (fileName) => {
      const uri = `${AUDIO_DIRECTORY}${fileName}`;
      const info = await FileSystem.getInfoAsync(uri);
      return {
        fileName,
        uri,
        sizeBytes: info.exists && 'size' in info ? (info.size ?? 0) : 0,
      };
    }),
  );
  return retained.filter((entry) => entry.sizeBytes > 44);
}

export async function clearRetainedWakeCommandAudio(): Promise<void> {
  const retained = await getRetainedWakeCommandAudio();
  await Promise.all(
    retained.map((entry) => FileSystem.deleteAsync(entry.uri, { idempotent: true })),
  );
}
