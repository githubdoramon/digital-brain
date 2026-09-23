package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.util.Base64
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig

/** The frozen v8 first stage. It owns one uninterrupted 16 kHz PCM stream. */
internal class V8KeywordSpotter(context: Context) {
  private val spotter: KeywordSpotter
  private var stream: OnlineStream
  private var samples = 0L
  private var nextAllowedSample = 0L
  private var acceptedSamplesTotal = 0L
  private var decodeCallsTotal = 0L
  private var keywordResultsTotal = 0L
  private var targetResultsTotal = 0L
  private var rejectResultsTotal = 0L
  private var lastKeywordResult = ""

  init {
    val prefix = "wake-word-v8/"
    val config = KeywordSpotterConfig(
      featConfig = FeatureConfig(sampleRate = 16_000, featureDim = 80, dither = 0f),
      modelConfig = OnlineModelConfig(
        transducer = OnlineTransducerModelConfig(
          encoder = "${prefix}encoder.onnx",
          decoder = "${prefix}decoder.onnx",
          joiner = "${prefix}joiner.onnx",
        ),
        tokens = "${prefix}tokens.txt",
        numThreads = 1,
        provider = "cpu",
      ),
      maxActivePaths = 16,
      keywordsFile = "${prefix}keywords.txt",
      keywordsScore = 1f,
      keywordsThreshold = 0.25f,
      numTrailingBlanks = 1,
    )
    spotter = KeywordSpotter(context.assets, config)
    stream = spotter.createStream()
  }

  @Synchronized
  fun acceptPcm16Base64(encoded: String): List<Map<String, Any>> {
    val bytes = Base64.decode(encoded, Base64.NO_WRAP)
    require(bytes.size % 2 == 0 && bytes.size <= 64 * 1024) { "Invalid wake PCM chunk" }
    val pcm = FloatArray(bytes.size / 2) { index ->
      val low = bytes[index * 2].toInt() and 0xff
      val high = bytes[index * 2 + 1].toInt()
      ((high shl 8) or low).toShort().toFloat() / 32768f
    }
    samples += pcm.size
    acceptedSamplesTotal += pcm.size
    stream.acceptWaveform(pcm, 16_000)
    val events = mutableListOf<Map<String, Any>>()
    while (spotter.isReady(stream)) {
      spotter.decode(stream)
      decodeCallsTotal += 1
      val keyword = spotter.getResult(stream).keyword
      if (keyword.isNotEmpty()) {
        keywordResultsTotal += 1
        lastKeywordResult = keyword
        if (keyword == "hey_brain" || keyword == "okay_brain") {
          targetResultsTotal += 1
        } else {
          rejectResultsTotal += 1
        }
        if ((keyword == "hey_brain" || keyword == "okay_brain") && samples >= nextAllowedSample) {
          events.add(mapOf("keyword" to keyword, "sampleIndex" to samples.toDouble()))
          nextAllowedSample = samples + 40_000L // Frozen v8 2.5-second candidate cooldown.
        }
        // Sherpa requires a stream reset after any keyword, including reject labels.
        spotter.reset(stream)
      }
    }
    return events
  }

  @Synchronized
  fun stats(): Map<String, Any> = mapOf(
    "streamSamples" to samples.toDouble(),
    "acceptedSamplesTotal" to acceptedSamplesTotal.toDouble(),
    "decodeCallsTotal" to decodeCallsTotal.toDouble(),
    "keywordResultsTotal" to keywordResultsTotal.toDouble(),
    "targetResultsTotal" to targetResultsTotal.toDouble(),
    "rejectResultsTotal" to rejectResultsTotal.toDouble(),
    "lastKeywordResult" to lastKeywordResult,
  )

  @Synchronized
  fun reset() {
    spotter.reset(stream)
    samples = 0L
    nextAllowedSample = 0L
  }

  @Synchronized
  fun release() {
    stream.release()
    spotter.release()
  }
}
