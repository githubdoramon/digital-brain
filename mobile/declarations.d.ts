declare module '*.svg' {
  import type { FunctionComponent } from 'react';
  import type { SvgProps } from 'react-native-svg';

  const content: FunctionComponent<SvgProps>;
  export default content;
}

declare module 'whisper.rn' {
  export type TranscribeFileOptions = {
    language?: string;
    maxThreads?: number;
    onProgress?: (progress: number) => void;
  };

  export type TranscribeResult = {
    result: string;
    language: string;
    segments: {
      text: string;
      t0: number;
      t1: number;
    }[];
    isAborted: boolean;
  };

  export class WhisperContext {
    transcribe(
      filePathOrBase64: string | number,
      options?: TranscribeFileOptions,
    ): {
      stop: () => Promise<void>;
      promise: Promise<TranscribeResult>;
    };
    transcribeData(
      data: ArrayBuffer,
      options?: TranscribeFileOptions,
    ): {
      stop: () => Promise<void>;
      promise: Promise<TranscribeResult>;
    };
    release(): Promise<void>;
  }

  export function initWhisper(options: {
    filePath: string | number;
    isBundleAsset?: boolean;
    useCoreMLIos?: boolean;
    useGpu?: boolean;
    useFlashAttn?: boolean;
  }): Promise<WhisperContext>;
}
