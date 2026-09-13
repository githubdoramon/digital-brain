package expo.modules.digitalbrainglassesalerts

import kotlin.math.PI
import kotlin.math.sin

/** Shared by the settings previews and automatic alerts. No Android or notification data. */
internal object GlassesAlertTone {
  const val SAMPLE_RATE = 48_000
  const val CALL_CYCLE_MS = 1_600
  const val NOTIFICATION_MS = 560

  fun notification(): ByteArray = render(NOTIFICATION_MS) { ms ->
    when {
      ms < 180 -> note(ms, 180.0, 1_040.0)
      ms in 240.0..<560.0 -> note(ms - 240, 320.0, 1_560.0)
      else -> 0.0
    }
  }

  // A sustained telephone-like warble, with only a short break between rings.
  fun call(): ByteArray = render(CALL_CYCLE_MS) { ms ->
    if (ms >= 1_200) 0.0 else {
      val envelope = (ms / 8).coerceAtMost(1.0) * ((1_200 - ms) / 12).coerceAtMost(1.0)
      val pulse = 0.75 + 0.25 * sin(2 * PI * 18 * ms / 1_000)
      val carrier = (sin(2 * PI * 740 * ms / 1_000) + sin(2 * PI * 980 * ms / 1_000)) / 2
      0.85 * envelope * pulse * carrier
    }
  }

  private fun note(ms: Double, duration: Double, frequency: Double): Double {
    val envelope = (ms / 8).coerceAtMost(1.0) * ((duration - ms) / 12).coerceAtMost(1.0)
    return 0.75 * envelope * sin(2 * PI * frequency * ms / 1_000)
  }

  private fun render(durationMs: Int, sample: (Double) -> Double): ByteArray {
    val frames = SAMPLE_RATE * durationMs / 1_000
    return ByteArray(frames * 2).also { pcm ->
      repeat(frames) { index ->
        val value = (sample(index * 1_000.0 / SAMPLE_RATE).coerceIn(-1.0, 1.0) * Short.MAX_VALUE).toInt()
        pcm[index * 2] = (value and 0xff).toByte()
        pcm[index * 2 + 1] = ((value shr 8) and 0xff).toByte()
      }
    }
  }
}
