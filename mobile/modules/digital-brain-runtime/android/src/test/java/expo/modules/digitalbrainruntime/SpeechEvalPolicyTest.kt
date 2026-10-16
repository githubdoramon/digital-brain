package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class SpeechEvalPolicyTest {
  private fun wav(rate: Int = 16000): ByteArray = ByteBuffer.allocate(48).order(ByteOrder.LITTLE_ENDIAN).apply {
    put("RIFF".toByteArray()); putInt(40); put("WAVEfmt ".toByteArray()); putInt(16)
    putShort(1); putShort(1); putInt(rate); putInt(rate * 2); putShort(2); putShort(16)
    put("data".toByteArray()); putInt(4); putShort(32767); putShort(-32768)
  }.array()
  @Test fun validatesWaveAndSignedSamples() {
    val samples = SpeechEvalPolicy.pcm(wav())
    assertEquals(2, samples.size)
    assertEquals(32767f / 32768, samples[0], 0.0001f)
    assertEquals(-1f, samples[1], 0.0001f)
  }
  @Test fun rejectsTruncatedWrongRateAndMalformedSizes() {
    listOf(wav(8000), wav().copyOf(47), wav().also { ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN).putInt(40, Int.MAX_VALUE) }).forEach {
      try { SpeechEvalPolicy.pcm(it); fail("accepted invalid WAV") } catch (_: IllegalArgumentException) { }
    }
  }
  @Test fun wordErrorsCountEdits() {
    assertEquals(0 to 4, SpeechEvalPolicy.errors("Hey Brain, take a", "hey brain take a"))
    assertEquals(1 to 4, SpeechEvalPolicy.errors("hey brain front gate", "hey brain car gate"))
    assertEquals(2 to 4, SpeechEvalPolicy.errors("hey brain front gate", "front gate"))
    assertEquals(2 to 0, SpeechEvalPolicy.errors("", "unexpected words"))
  }
  @Test fun percentilesPreserveEmptyAndTail() {
    assertNull(SpeechEvalPolicy.percentile(emptyList(), .95))
    assertEquals(95L, SpeechEvalPolicy.percentile((1L..100L).toList(), .95))
  }
  @Test fun timingIncludesFailedLoadAndSeparatesInference() {
    var clock = 20L
    val timing = SpeechTiming({ clock }, 0)
    timing.measure("modelLoadMs") { clock += 80 }
    timing.measure("inferenceMs") { clock += 30 }
    assertEquals(80L, timing.duration("modelLoadMs"))
    assertEquals(30L, timing.duration("inferenceMs"))
    assertEquals(130L, timing.elapsed())
    try { timing.measure("failureMs") { clock += 12; throw IllegalStateException() } } catch (_: IllegalStateException) { }
    assertEquals(12L, timing.duration("failureMs"))
  }
}
