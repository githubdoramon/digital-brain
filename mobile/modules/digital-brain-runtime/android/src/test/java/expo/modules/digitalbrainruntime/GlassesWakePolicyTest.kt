package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class GlassesWakePolicyTest {
  private class Detector : GlassesWakeDetector {
    var fire = false
    var calls = 0
    var resets = 0
    var received = 0
    override fun accept(pcm: ByteArray): Boolean { calls++; received += pcm.size; return fire.also { fire = false } }
    override fun reset() { resets++ }
    override fun release() {}
  }
  private fun tone(amplitude: Int, samples: Int = 320) = ByteArray(samples * 2).also { pcm ->
    repeat(samples) { i -> pcm[i * 2] = amplitude.toByte(); pcm[i * 2 + 1] = (amplitude shr 8).toByte() }
  }
  @Test fun vadAndQuietGatePrecedeDetectorAndReplayOnset() {
    val detector = Detector(); val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.pcm(tone(2000), 10000, 100, 50)
    assertEquals(0, detector.calls)
    policy.vad(true, 100)
    repeat(10) { policy.pcm(tone(10), 10000 + it * 20L, 100 + it * 20L, 50) }
    assertEquals(0, detector.calls)
    repeat(5) { policy.pcm(tone(2000), 10200 + it * 20L, 300 + it * 20L, 50) }
    assertEquals(1, detector.calls)
    assertEquals(15 * 640, detector.received)
    assertTrue(out.isEmpty())
  }
  @Test fun wakesCapturePhraseAndImmediateCommandThenStopAfterSilence() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    assertTrue(policy.capturing)
    policy.pcm(tone(3000), 10020, 120, 0)
    policy.vad(false, 140); policy.tick(11519, 1639)
    assertTrue(out.isEmpty())
    policy.tick(11520, 1640)
    assertEquals(1280, out.single().chunk.pcm.size)
    assertEquals(9980L, out.single().chunk.startedAtMs)
    assertEquals(WakeEnd.SILENCE, out.single().reason)
  }
  @Test fun repeatedWakesDuringCaptureDoNotSplitItAndCooldownLastsSixSeconds() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>(); var wakes = 0
    val policy = GlassesWakePolicy(detector, { wakes++ }, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    detector.fire = true; policy.pcm(tone(2000), 10020, 120, 0)
    assertEquals(1, detector.calls)
    policy.vad(false, 140); policy.tick(11520, 1640)
    policy.vad(true, 2000); policy.pcm(tone(2000), 11900, 2000, 0)
    assertEquals(1, wakes)
    policy.vad(false, 2100); policy.tick(13600, 3600)
    policy.vad(true, 6100); policy.pcm(tone(2000), 16000, 6100, 0)
    assertEquals(2, wakes)
  }
  @Test fun interruptionsDiscardCaptureWithoutEmittingFiles() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    policy.discard(); policy.tick(12000, 2100)
    assertFalse(policy.capturing); assertTrue(out.isEmpty())
  }
  @Test fun hardTimeoutAndAudioSizeAreBounded() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    repeat(15) { policy.pcm(tone(2000, 32000), 10020 + it * 1000L, 120 + it * 1000L, 0) }
    assertEquals(GlassesWakePolicy.MAX_BYTES, out.single().chunk.pcm.size)
    assertEquals(WakeEnd.LIMIT, out.single().reason)
  }
  @Test fun unknownTransportLossDropsPartialInsteadOfInventingSilence() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    policy.tick(13000, 3100)
    assertFalse(policy.capturing); assertTrue(out.isEmpty())
  }
  @Test fun suppressedSilenceProvidesDecoderBlanksWithoutSavingSyntheticAudio() {
    val detector = Detector(); val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    detector.fire = true; policy.vad(false, 120); policy.tick(10120, 220)
    assertTrue(policy.capturing)
    policy.tick(11520, 1620)
    assertEquals(640, out.single().chunk.pcm.size)
  }
  @Test fun thirtySecondWallLimitCompletesDespiteLowAudioDelivery() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    repeat(29) { policy.pcm(tone(2000), 11000 + it * 1000L, 1100 + it * 1000L, 0) }
    policy.tick(40000, 30100)
    assertEquals(WakeEnd.LIMIT, out.single().reason)
  }
  @Test fun prolongedIdleDoesNotKeepRecreatingStreams() {
    val detector = Detector()
    val policy = GlassesWakePolicy(detector, {}, {}, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    policy.vad(false, 120); policy.tick(11520, 1620)
    val resets = detector.resets
    repeat(100) { policy.tick(12000 + it * 1000L, 2100 + it * 1000L) }
    assertEquals(resets, detector.resets)
  }
  @Test fun pcmRingKeepsOnlyRecentBytesInOrder() {
    val ring = PcmRing(4); ring.write(byteArrayOf(1, 2, 3)); ring.write(byteArrayOf(4, 5, 6))
    assertArrayEquals(byteArrayOf(3, 4, 5, 6), ring.toByteArray())
    ring.reset(); assertEquals(0, ring.toByteArray().size)
  }
  @Test fun wavHeaderDescribesOriginalLittleEndianPcm() {
    val pcm = tone(1234)
    val wav = GlassesWakeStore.wav(pcm)
    val header = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN)
    assertEquals("RIFF", String(wav.copyOfRange(0, 4)))
    assertEquals(16000, header.getInt(24)); assertEquals(16, header.getShort(34).toInt())
    assertEquals(pcm.size, header.getInt(40)); assertArrayEquals(pcm, wav.copyOfRange(44, wav.size))
  }
}
