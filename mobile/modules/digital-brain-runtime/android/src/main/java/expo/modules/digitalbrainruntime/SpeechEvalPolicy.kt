package expo.modules.digitalbrainruntime

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.ceil

/** Pure replay validation/scoring. This layer cannot execute commands. */
internal object SpeechEvalPolicy {
  const val VERSION = 1
  const val MAX_WAV_BYTES = 30 * 32000 + 4096
  fun pcm(wav: ByteArray): FloatArray {
    require(wav.size in 44..MAX_WAV_BYTES) { "invalid_audio" }
    fun ascii(offset: Int, size: Int) = String(wav, offset, size, Charsets.US_ASCII)
    require(ascii(0, 4) == "RIFF" && ascii(8, 4) == "WAVE") { "invalid_audio" }
    val view = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN)
    var offset = 12
    var format = false
    var audio: ByteArray? = null
    while (offset + 8 <= wav.size) {
      val size = view.getInt(offset + 4)
      require(size >= 0 && size <= wav.size - offset - 8) { "invalid_audio" }
      when (ascii(offset, 4)) {
        "fmt " -> {
          require(size >= 16 && view.getShort(offset + 8).toInt() == 1 &&
            view.getShort(offset + 10).toInt() == 1 && view.getInt(offset + 12) == 16000 &&
            view.getShort(offset + 22).toInt() == 16) { "invalid_audio" }
          format = true
        }
        "data" -> { require(audio == null) { "invalid_audio" }; audio = wav.copyOfRange(offset + 8, offset + 8 + size) }
      }
      offset += 8 + size + size % 2
    }
    val bytes = requireNotNull(audio) { "invalid_audio" }
    require(format && bytes.isNotEmpty() && bytes.size % 2 == 0 && bytes.size <= 30 * 32000) { "invalid_audio" }
    return FloatArray(bytes.size / 2) { i ->
      (((bytes[i * 2].toInt() and 255) or (bytes[i * 2 + 1].toInt() shl 8)).toShort().toFloat()) / 32768f
    }
  }
  fun words(text: String) = text.lowercase(java.util.Locale.ROOT).replace(Regex("[^a-z0-9']+"), " ").trim()
    .split(Regex("\\s+")).filter { it.isNotBlank() }
  fun errors(reference: String, actual: String): Pair<Int, Int> {
    val a = words(reference); val b = words(actual)
    var previous = IntArray(b.size + 1) { it }
    a.forEachIndexed { i, word ->
      val next = IntArray(b.size + 1); next[0] = i + 1
      b.forEachIndexed { j, other -> next[j + 1] = minOf(next[j] + 1, previous[j + 1] + 1, previous[j] + if (word == other) 0 else 1) }
      previous = next
    }
    return previous[b.size] to a.size
  }
  fun percentile(values: List<Long>, p: Double): Long? = if (values.isEmpty()) null
    else values.sorted()[(ceil(values.size * p).toInt() - 1).coerceIn(0, values.lastIndex)]
}
