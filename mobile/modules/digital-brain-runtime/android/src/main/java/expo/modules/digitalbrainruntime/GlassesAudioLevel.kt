package expo.modules.digitalbrainruntime

import kotlin.math.log10
import kotlin.math.sqrt

/** Digital PCM level, not a calibrated sound-pressure measurement. */
internal object GlassesAudioLevel {
  fun db(pcm: ByteArray, start: Int = 0, end: Int = pcm.size): Double {
    var sum = 0.0
    var count = 0
    var i = start
    while (i + 1 < end) {
      val sample = ((pcm[i].toInt() and 255) or (pcm[i + 1].toInt() shl 8)).toShort().toDouble() / 32768.0
      sum += sample * sample; count++; i += 2
    }
    return if (count == 0 || sum == 0.0) -96.0 else 20 * log10(sqrt(sum / count))
  }
  fun display(db: Double): Int = (((db + 60) / 40) * 100).toInt().coerceIn(0, 100)
  fun accepts(pcm: ByteArray, minimum: Int): Boolean {
    if (minimum == 0) return true
    val threshold = -60.0 + minimum * 0.4
    var sustained = 0
    var offset = 0
    // Five consecutive 20ms windows reject isolated clicks without cutting quiet word endings.
    while (offset + 640 <= pcm.size) {
      sustained = if (db(pcm, offset, offset + 640) >= threshold) sustained + 1 else 0
      if (sustained >= 5) return true
      offset += 640
    }
    return false
  }
}
