package expo.modules.digitalbrainruntime

import android.content.Context
import com.k2fsa.sherpa.onnx.*

/** Personal acoustic model: 2026-10-15 epoch 3. See the asset model-manifest.json. */
internal class GlassesSherpaDetector(c: Context) : GlassesWakeDetector {
  private val spotter = KeywordSpotter(c.assets, KeywordSpotterConfig(
    featConfig = FeatureConfig(sampleRate = 16000, featureDim = 80, dither = 0f),
    modelConfig = OnlineModelConfig(
      transducer = OnlineTransducerModelConfig(encoder = "wake-word-personal-2026-10-15/encoder.onnx",
        decoder = "wake-word-personal-2026-10-15/decoder.onnx", joiner = "wake-word-personal-2026-10-15/joiner.onnx"),
      tokens = "wake-word-personal-2026-10-15/tokens.txt", numThreads = 1, provider = "cpu"),
    maxActivePaths = 16, keywordsFile = "wake-word-personal-2026-10-15/keywords.txt", keywordsScore = 0.000001f,
    keywordsThreshold = 0.35f, numTrailingBlanks = 1))
  private var stream = spotter.createStream()
  private val frame = ByteArray(640)
  private var used = 0
  override fun accept(pcm: ByteArray): Boolean {
    var offset = 0
    var accepted = false
    while (offset < pcm.size) {
      val n = minOf(frame.size - used, pcm.size - offset)
      pcm.copyInto(frame, used, offset, offset + n); used += n; offset += n
      if (used != frame.size) continue
      used = 0
      val samples = FloatArray(320) { i ->
        ((frame[i * 2].toInt() and 255) or (frame[i * 2 + 1].toInt() shl 8)).toShort().toFloat() / 32768f
      }
      stream.acceptWaveform(samples, 16000)
      while (spotter.isReady(stream)) {
        spotter.decode(stream)
        val keyword = spotter.getResult(stream).keyword
        if (keyword.isNotEmpty()) {
          if (keyword == "hey_brain" || keyword == "okay_brain") accepted = true
          spotter.reset(stream)
        }
      }
    }
    return accepted
  }
  override fun reset() { stream.release(); stream = spotter.createStream(); used = 0 }
  override fun release() { stream.release(); spotter.release() }
}
