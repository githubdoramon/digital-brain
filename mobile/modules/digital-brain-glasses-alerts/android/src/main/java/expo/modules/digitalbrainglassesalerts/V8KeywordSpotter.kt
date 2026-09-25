package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.os.SystemClock
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
  private val frameBytes = ByteArray(640)
  private var frameByteCount = 0
  private var feedCallsTotal = 0L
  private var feedTimeMsTotal = 0.0
  private var feedTimeMsMax = 0.0
  private var slowFeedCount = 0L

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
  fun acceptPcm16Bytes(bytes: ByteArray): List<Map<String, Any>> {
    require(bytes.size % 2 == 0 && bytes.size <= 64 * 1024) { "Invalid wake PCM chunk" }
    val started = SystemClock.elapsedRealtimeNanos()
    feedCallsTotal += 1
    val events = mutableListOf<Map<String, Any>>()
    var offset = 0
    while (offset < bytes.size) {
      val count = minOf(frameBytes.size - frameByteCount, bytes.size - offset)
      bytes.copyInto(frameBytes, frameByteCount, offset, offset + count)
      frameByteCount += count
      offset += count
      if (frameByteCount == frameBytes.size) {
        val pcm = FloatArray(320) { index ->
          val low = frameBytes[index * 2].toInt() and 0xff
          val high = frameBytes[index * 2 + 1].toInt()
          ((high shl 8) or low).toShort().toFloat() / 32768f
        }
        frameByteCount = 0
        samples += pcm.size
        acceptedSamplesTotal += pcm.size
        stream.acceptWaveform(pcm, 16_000)
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
              nextAllowedSample = samples + 40_000L
            }
            spotter.reset(stream)
          }
        }
      }
    }
    val elapsedMs = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000.0
    feedTimeMsTotal += elapsedMs
    feedTimeMsMax = maxOf(feedTimeMsMax, elapsedMs)
    if (elapsedMs > bytes.size / 32.0) slowFeedCount += 1
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
    "feedCallsTotal" to feedCallsTotal.toDouble(),
    "feedTimeMsTotal" to feedTimeMsTotal,
    "feedTimeMsMax" to feedTimeMsMax,
    "slowFeedCount" to slowFeedCount.toDouble(),
    "partialFrameBytes" to frameByteCount,
  )

  @Synchronized
  fun reset() {
    spotter.reset(stream)
    samples = 0L
    nextAllowedSample = 0L
    frameByteCount = 0
  }

  @Synchronized
  fun release() {
    stream.release()
    spotter.release()
  }
}
