export interface OnnxTensorLike {
  data: Float32Array | ArrayLike<number>;
}

export interface OnnxSessionLike {
  run(feeds: Record<string, unknown>): Promise<Record<string, OnnxTensorLike>>;
}

export interface OnnxRuntimeLike {
  Tensor: new (type: 'float32', data: Float32Array, dims: number[]) => unknown;
  InferenceSession: {
    create(path: string): Promise<OnnxSessionLike>;
  };
}

export interface SpeechEmbeddingBackend {
  readonly embeddingSize: number;
  acceptPcm16(chunk: Int16Array): Promise<Float32Array[]>;
  reset(): void;
}

function firstOutput(outputs: Record<string, OnnxTensorLike>): Float32Array {
  const output = Object.values(outputs)[0];
  if (!output) throw new Error('ONNX model returned no outputs');
  return output.data instanceof Float32Array ? output.data : Float32Array.from(output.data);
}

function appendInt16(
  left: Int16Array<ArrayBufferLike>,
  right: Int16Array<ArrayBufferLike>,
): Int16Array<ArrayBufferLike> {
  const output = new Int16Array(left.length + right.length);
  output.set(left);
  output.set(right, left.length);
  return output;
}

export class OpenWakeWordOnnxBackend implements SpeechEmbeddingBackend {
  readonly embeddingSize = 96;
  private pending: Int16Array<ArrayBufferLike> = new Int16Array(0);
  private previousPcm: Int16Array<ArrayBufferLike> = new Int16Array(0);
  private melFrames = new Float32Array(76 * 32).fill(1);

  private constructor(
    private readonly ort: OnnxRuntimeLike,
    private readonly melspectrogram: OnnxSessionLike,
    private readonly embedding: OnnxSessionLike,
    private readonly hopSamples: number,
  ) {}

  static async create(
    ort: OnnxRuntimeLike,
    melspectrogramModelPath: string,
    embeddingModelPath: string,
    hopSamples = 1280,
  ): Promise<OpenWakeWordOnnxBackend> {
    const [melspectrogram, embedding] = await Promise.all([
      ort.InferenceSession.create(melspectrogramModelPath),
      ort.InferenceSession.create(embeddingModelPath),
    ]);
    return new OpenWakeWordOnnxBackend(ort, melspectrogram, embedding, hopSamples);
  }

  async acceptPcm16(chunk: Int16Array): Promise<Float32Array[]> {
    this.pending = appendInt16(this.pending, chunk);
    const embeddings: Float32Array[] = [];
    while (this.pending.length >= this.hopSamples) {
      const current = this.pending.slice(0, this.hopSamples);
      this.pending = this.pending.slice(this.hopSamples);
      embeddings.push(await this.processHop(current));
    }
    return embeddings;
  }

  reset(): void {
    this.pending = new Int16Array(0);
    this.previousPcm = new Int16Array(0);
    this.melFrames = new Float32Array(76 * 32).fill(1);
  }

  private async processHop(chunk: Int16Array): Promise<Float32Array> {
    const pcm = appendInt16(this.previousPcm, chunk);
    const pcmFloat = Float32Array.from(pcm);
    const melInput = new this.ort.Tensor('float32', pcmFloat, [1, pcmFloat.length]);
    const rawMel = firstOutput(await this.melspectrogram.run({ input: melInput }));
    if (rawMel.length % 32 !== 0) throw new Error(`Unexpected mel output length ${rawMel.length}`);

    const transformed = new Float32Array(rawMel.length);
    for (let index = 0; index < rawMel.length; index += 1)
      transformed[index] = rawMel[index] / 10 + 2;
    const combined = new Float32Array(this.melFrames.length + transformed.length);
    combined.set(this.melFrames);
    combined.set(transformed, this.melFrames.length);
    this.melFrames = combined.slice(combined.length - 76 * 32);

    const embeddingInput = new this.ort.Tensor('float32', this.melFrames, [1, 76, 32, 1]);
    const embedding = firstOutput(await this.embedding.run({ input_1: embeddingInput }));
    if (embedding.length !== this.embeddingSize) {
      throw new Error(`Unexpected speech embedding length ${embedding.length}`);
    }
    this.previousPcm = pcm.slice(Math.max(0, pcm.length - 480));
    return embedding.slice();
  }
}
