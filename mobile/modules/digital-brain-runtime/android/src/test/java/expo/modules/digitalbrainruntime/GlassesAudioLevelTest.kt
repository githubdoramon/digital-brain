package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesAudioLevelTest {
  private fun tone(amplitude: Int, samples: Int = 8000): ByteArray = ByteArray(samples * 2).also { pcm ->
    repeat(samples) { i ->
      val value = if (i % 2 == 0) amplitude else -amplitude
      pcm[i * 2] = value.toByte(); pcm[i * 2 + 1] = (value shr 8).toByte()
    }
  }
  @Test fun quietSpeechIsRejectedAndNormalSpeechPasses() {
    assertFalse(GlassesAudioLevel.accepts(tone(100), 50))
    assertTrue(GlassesAudioLevel.accepts(tone(1000), 50))
  }
  @Test fun offPreservesQuietSpeech() { assertTrue(GlassesAudioLevel.accepts(tone(1), 0)) }
  @Test fun aLoudClickDoesNotQualifyAQuietSegment() {
    val pcm = tone(10); tone(20000, 320).copyInto(pcm)
    assertFalse(GlassesAudioLevel.accepts(pcm, 50))
  }
  @Test fun sustainedSpeechPassesDespiteQuietWordEndings() {
    val pcm = tone(10); tone(1000, 1600).copyInto(pcm)
    assertTrue(GlassesAudioLevel.accepts(pcm, 50))
  }
  @Test fun levelScaleHandlesSilenceAndSignedPcm() {
    assertEquals(0, GlassesAudioLevel.display(GlassesAudioLevel.db(ByteArray(640))))
    assertEquals(100, GlassesAudioLevel.display(GlassesAudioLevel.db(tone(32767))))
    assertEquals(GlassesAudioLevel.db(tone(1000)), GlassesAudioLevel.db(tone(-1000)), 0.001)
  }
}
