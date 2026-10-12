package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesSpeechSegmenterTest {
  @Test fun silenceDoesNotProduceTranscriptionWork() {
    val out = mutableListOf<SpeechChunk>()
    val segmenter = GlassesSpeechSegmenter(out::add)
    repeat(100) { segmenter.pcm(ByteArray(640) { 1 }, 1000L + it * 20, it * 20L) }
    segmenter.finish()
    assertTrue(out.isEmpty())
  }
  @Test fun shortPauseContinuesTheSamePhraseAndSilenceCompletesIt() {
    val out = mutableListOf<SpeechChunk>()
    val segmenter = GlassesSpeechSegmenter(out::add)
    segmenter.vad(true, 0)
    segmenter.pcm(ByteArray(16000) { 1 }, 1000, 0)
    segmenter.vad(false, 500)
    segmenter.tick(1000)
    assertTrue(out.isEmpty())
    segmenter.vad(true, 1000)
    segmenter.pcm(ByteArray(16000) { 1 }, 2000, 1000)
    segmenter.vad(false, 1500)
    segmenter.tick(2400)
    assertEquals(1, out.size)
    assertEquals(1000L, out.single().startedAtMs)
    assertEquals(32000, out.single().pcm.size)
  }
  @Test fun aMissingVadEndCannotGrowTheBufferBeyondTwentySeconds() {
    val out = mutableListOf<SpeechChunk>()
    val segmenter = GlassesSpeechSegmenter(out::add)
    segmenter.vad(true, 0)
    repeat(2100) { segmenter.pcm(ByteArray(640) { 1 }, 1000 + it * 20L, it * 20L) }
    segmenter.finish()
    assertEquals(listOf(640000, 640000, 64000), out.map { it.pcm.size })
  }
  @Test fun interruptionFlushesReceivedSpeechButRejectsVeryShortTriggers() {
    val out = mutableListOf<SpeechChunk>()
    val segmenter = GlassesSpeechSegmenter(out::add)
    segmenter.vad(true, 0); segmenter.pcm(ByteArray(640) { 1 }, 1000, 0); segmenter.finish()
    assertTrue(out.isEmpty())
    segmenter.vad(true, 1000); segmenter.pcm(ByteArray(16000) { 1 }, 2000, 1000); segmenter.finish()
    assertEquals(1, out.size)
  }
  @Test fun aFalseSpeechTriggerWithOnlySilenceDoesNotReachWhisper() {
    val out = mutableListOf<SpeechChunk>()
    val segmenter = GlassesSpeechSegmenter(out::add)
    segmenter.vad(true, 0); segmenter.pcm(ByteArray(16000), 1000, 0); segmenter.finish()
    assertTrue(out.isEmpty())
  }
  @Test fun missingPcmCompletesThePhraseWithoutStartingNewCapture() {
    val out = mutableListOf<SpeechChunk>()
    val segmenter = GlassesSpeechSegmenter(out::add)
    segmenter.vad(true, 0); segmenter.pcm(ByteArray(16000) { 1 }, 1000, 0)
    segmenter.tick(2000)
    segmenter.pcm(ByteArray(16000) { 1 }, 4000, 3000)
    assertEquals(1, out.size)
    assertFalse(segmenter.collecting)
  }
}
