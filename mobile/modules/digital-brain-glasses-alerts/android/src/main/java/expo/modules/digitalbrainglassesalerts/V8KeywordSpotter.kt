package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.os.SystemClock
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

internal data class V8CommandAudioCapture(
  val pcm: ByteArray,
  val startSampleIndex: Long,
  val endSampleIndex: Long,
  val ambientRms: List<Double>,
)

/** The frozen v8 first stage. It owns one uninterrupted 16 kHz PCM stream. */
internal class V8KeywordSpotter(context: Context) {
  companion object {
    private const val PCM_HISTORY_SAMPLES = 8 * 16_000
    private const val AMBIENT_RMS_HISTORY_SIZE = 120
    private const val MIN_AMBIENT_RMS = 0.002
  }

  private val spotter: KeywordSpotter
  private var stream: OnlineStream
  private var samples = 0L
  private var receivedSamples = 0L
  private var nextAllowedSample = 0L
  private var acceptedSamplesTotal = 0L
  private var decodeCallsTotal = 0L
  private var keywordResultsTotal = 0L
  private var targetResultsTotal = 0L
  private var rejectResultsTotal = 0L
  private var lastKeywordResult = ""
  private val frameBytes = ByteArray(640)
  private var frameByteCount = 0
  private val pcmHistory = ByteArray(PCM_HISTORY_SAMPLES * 2)
  private val ambientFilter = AmbientPcmFilter()
  private val ambientRms = ArrayDeque<Double>(AMBIENT_RMS_HISTORY_SIZE)
  private var ambientSquaredSinceFrame = 0.0
  private var ambientSamplesSinceFrame = 0
  private var pcmSamplesSinceSnapshot = 0L
  private var pcmSquaredAmplitudeSinceSnapshot = 0.0
  private var pcmPeakSinceSnapshot = 0
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
    appendPcmHistory(bytes)
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
          val sample = ((high shl 8) or low).toShort()
          val sampleValue = sample.toInt()
          val normalized = sampleValue / 32768.0
          val filtered = ambientFilter.process(normalized).coerceIn(-1.0, 0.999969482421875)
          ambientSquaredSinceFrame += filtered * filtered
          ambientSamplesSinceFrame += 1
          pcmSamplesSinceSnapshot += 1
          pcmSquaredAmplitudeSinceSnapshot += sampleValue.toDouble() * sampleValue.toDouble()
          pcmPeakSinceSnapshot = max(pcmPeakSinceSnapshot, kotlin.math.abs(sampleValue))
          normalized.toFloat()
        }
        frameByteCount = 0
        samples += pcm.size
        acceptedSamplesTotal += pcm.size
        val ambientLevel = sqrt(ambientSquaredSinceFrame / ambientSamplesSinceFrame)
        if (ambientLevel >= MIN_AMBIENT_RMS) {
          ambientRms.addLast(ambientLevel)
          while (ambientRms.size > AMBIENT_RMS_HISTORY_SIZE) ambientRms.removeFirst()
        }
        ambientSquaredSinceFrame = 0.0
        ambientSamplesSinceFrame = 0
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
    "receivedSamples" to receivedSamples.toDouble(),
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
    "wakeDetectorSamplesSinceSnapshot" to pcmSamplesSinceSnapshot.toDouble(),
    "wakeDetectorRmsSinceSnapshot" to if (pcmSamplesSinceSnapshot > 0) {
      sqrt(pcmSquaredAmplitudeSinceSnapshot / pcmSamplesSinceSnapshot) / 32768.0
    } else {
      0.0
    },
    "wakeDetectorPeakSinceSnapshot" to pcmPeakSinceSnapshot.toDouble() / 32768.0,
    "ambientRmsCount" to ambientRms.size,
  ).also {
    pcmSamplesSinceSnapshot = 0L
    pcmSquaredAmplitudeSinceSnapshot = 0.0
    pcmPeakSinceSnapshot = 0
  }

  @Synchronized
  fun wakeAudioRange(startSampleIndex: Long, endSampleIndex: Long): ByteArray {
    require(startSampleIndex >= 0 && endSampleIndex >= startSampleIndex) {
      "Invalid V8 wake audio range"
    }
    require(endSampleIndex <= receivedSamples) { "V8 wake audio range is not available yet" }
    require(startSampleIndex >= max(0L, receivedSamples - PCM_HISTORY_SAMPLES)) {
      "V8 wake audio history no longer contains the requested range"
    }
    val byteCount = Math.multiplyExact((endSampleIndex - startSampleIndex).toInt(), 2)
    val output = ByteArray(byteCount)
    var sourceSample = startSampleIndex
    var outputOffset = 0
    while (sourceSample < endSampleIndex) {
      val sourceByteOffset = ((sourceSample % PCM_HISTORY_SAMPLES) * 2).toInt()
      val count = min(
        output.size - outputOffset,
        pcmHistory.size - sourceByteOffset,
      )
      pcmHistory.copyInto(output, outputOffset, sourceByteOffset, sourceByteOffset + count)
      outputOffset += count
      sourceSample += count / 2
    }
    return output
  }

  @Synchronized
  fun beginCommandCapture(startSampleIndex: Long): V8CommandAudioCapture {
    val endSampleIndex = receivedSamples
    val capture = V8CommandAudioCapture(
      pcm = wakeAudioRange(startSampleIndex, endSampleIndex),
      startSampleIndex = startSampleIndex,
      endSampleIndex = endSampleIndex,
      ambientRms = ambientRms.toList(),
    )
    spotter.reset(stream)
    frameByteCount = 0
    return capture
  }

  private fun appendPcmHistory(bytes: ByteArray) {
    var sourceOffset = 0
    while (sourceOffset < bytes.size) {
      val destinationOffset = ((receivedSamples % PCM_HISTORY_SAMPLES) * 2).toInt()
      val count = min(bytes.size - sourceOffset, pcmHistory.size - destinationOffset)
      bytes.copyInto(pcmHistory, destinationOffset, sourceOffset, sourceOffset + count)
      receivedSamples += count / 2
      sourceOffset += count
    }
  }

  @Synchronized
  fun reset() {
    spotter.reset(stream)
    samples = 0L
    receivedSamples = 0L
    nextAllowedSample = 0L
    frameByteCount = 0
    pcmHistory.fill(0)
    ambientFilter.reset()
    ambientRms.clear()
    ambientSquaredSinceFrame = 0.0
    ambientSamplesSinceFrame = 0
    pcmSamplesSinceSnapshot = 0L
    pcmSquaredAmplitudeSinceSnapshot = 0.0
    pcmPeakSinceSnapshot = 0
  }

  @Synchronized
  fun release() {
    stream.release()
    spotter.release()
  }
}

private class AmbientPcmFilter {
  private val highPass = Biquad("highpass", 120.0, 16_000.0)
  private val lowPass = Biquad("lowpass", 7_000.0, 16_000.0)

  fun process(sample: Double): Double = lowPass.process(highPass.process(sample))

  fun reset() {
    highPass.reset()
    lowPass.reset()
  }
}

private class Biquad(type: String, frequencyHz: Double, sampleRateHz: Double) {
  private val b0: Double
  private val b1: Double
  private val b2: Double
  private val a1: Double
  private val a2: Double
  private var x1 = 0.0
  private var x2 = 0.0
  private var y1 = 0.0
  private var y2 = 0.0

  init {
    val omega = (2.0 * PI * frequencyHz) / sampleRateHz
    val cosine = cos(omega)
    val alpha = sin(omega) / (2.0 * kotlin.math.sqrt(0.5))
    val a0 = 1.0 + alpha
    val a1Value = -2.0 * cosine
    val a2Value = 1.0 - alpha
    val common = if (type == "highpass") (1.0 + cosine) / 2.0 else (1.0 - cosine) / 2.0
    val numeratorB0 = common
    val numeratorB1 = if (type == "highpass") -(1.0 + cosine) else 1.0 - cosine
    val numeratorB2 = common
    b0 = numeratorB0 / a0
    b1 = numeratorB1 / a0
    b2 = numeratorB2 / a0
    a1 = a1Value / a0
    a2 = a2Value / a0
  }

  fun process(sample: Double): Double {
    val output = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
    x2 = x1
    x1 = sample
    y2 = y1
    y1 = output
    return output
  }

  fun reset() {
    x1 = 0.0
    x2 = 0.0
    y1 = 0.0
    y2 = 0.0
  }
}
