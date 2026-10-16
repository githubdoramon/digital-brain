package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class GlassesFeedbackToneTest {
  private fun samples(sound: GlassesFeedbackSound): ShortArray {
    val bytes = GlassesAlertTone.feedback(sound)
    val data = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
    return ShortArray(data.remaining()).also { data.get(it) }
  }
  @Test fun cuesAreShortAudibleAndDoNotClip() {
    for (sound in GlassesFeedbackSound.entries) {
      val pcm = samples(sound)
      assertTrue(pcm.size * 1000L / GlassesAlertTone.SAMPLE_RATE in 100..300)
      assertEquals(0, pcm.first().toInt())
      assertTrue(pcm.any { kotlin.math.abs(it.toInt()) > 10000 })
      assertTrue(pcm.all { kotlin.math.abs(it.toInt()) < Short.MAX_VALUE })
      assertTrue(kotlin.math.abs(pcm.last().toInt()) < 100)
    }
  }
  @Test fun executionHasTwoBurstsSeparatedBySilenceAndDiffersFromWakeAndNotification() {
    val wake = samples(GlassesFeedbackSound.WAKE)
    val command = samples(GlassesFeedbackSound.COMMAND)
    fun range(fromMs: Int, toMs: Int) = command.sliceArray(
      fromMs * GlassesAlertTone.SAMPLE_RATE / 1000 until toMs * GlassesAlertTone.SAMPLE_RATE / 1000)
    assertTrue(range(10, 80).any { it.toInt() != 0 })
    assertTrue(range(90, 150).all { it.toInt() == 0 })
    assertTrue(range(160, 270).any { it.toInt() != 0 })
    assertFalse(wake.contentEquals(command))
    assertFalse(GlassesAlertTone.feedback(GlassesFeedbackSound.COMMAND).contentEquals(GlassesAlertTone.notification()))
  }
}
