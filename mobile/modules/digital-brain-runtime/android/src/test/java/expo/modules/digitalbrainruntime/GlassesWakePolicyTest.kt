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
    repeat(15) { policy.pcm(tone(3000), 10020 + it * 20L, 120 + it * 20L, 0) }
    policy.vad(false, 420); policy.tick(11799, 1919)
    assertTrue(out.isEmpty())
    policy.tick(11800, 1920)
    assertEquals(16 * 640, out.single().chunk.pcm.size)
    assertEquals(9980L, out.single().chunk.startedAtMs)
    assertEquals(WakeEnd.SILENCE, out.single().reason)
  }
  @Test fun repeatedWakesDuringCaptureDoNotSplitItAndCooldownLastsSixSeconds() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>(); var wakes = 0
    val policy = GlassesWakePolicy(detector, { wakes++ }, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    detector.fire = true; policy.pcm(tone(2000), 10020, 120, 0)
    assertEquals(1, detector.calls)
    policy.vad(false, 140); policy.tick(14020, 4140)
    policy.vad(true, 4500); policy.pcm(tone(2000), 14400, 4500, 0)
    assertEquals(1, wakes)
    policy.vad(false, 4600); policy.tick(16000, 6100)
    policy.vad(true, 6100); policy.pcm(tone(2000), 16000, 6100, 0)
    assertEquals(2, wakes)
  }
  @Test fun initialWakeSilenceWaitsFourSecondsWithoutMistakingVadSilenceForTransportLoss() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    policy.vad(false, 120)
    policy.tick(13000, 3100)
    assertTrue(policy.capturing); assertTrue(out.isEmpty())
    assertTrue(policy.waitMs(3100) > 1)
    policy.tick(14019, 4119); assertTrue(out.isEmpty())
    policy.tick(14020, 4120); assertEquals(WakeEnd.SILENCE, out.single().reason)
  }
  @Test fun delayedCommandSwitchesToShortSilenceAndKeepsAllReceivedAudio() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    policy.vad(false, 120); policy.tick(13000, 3100)
    policy.vad(true, 3500)
    repeat(15) { policy.pcm(tone(3000), 13400 + it * 20L, 3500 + it * 20L, 0) }
    policy.vad(false, 3800)
    policy.tick(15199, 5299); assertTrue(out.isEmpty())
    policy.tick(15200, 5300)
    assertEquals(16 * 640, out.single().chunk.pcm.size)
    assertEquals(WakeEnd.SILENCE, out.single().reason)
  }
  @Test fun quietCommandBelowWakeThresholdKeepsCaptureOpenAndUsesShortVadSilence() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    val loud = tone(32767); val soft = tone(10)
    policy.vad(true, 100)
    repeat(5) { policy.pcm(loud, 10000 + it * 20L, 100 + it * 20L, 100) }
    assertTrue(policy.capturing)
    policy.vad(false, 200)
    policy.vad(true, 3500)
    // More than both endpoint deadlines, below even the lowest enabled wake threshold.
    repeat(250) { policy.pcm(soft, 13400 + it * 20L, 3500 + it * 20L, 100) }
    assertTrue(policy.capturing); assertTrue(out.isEmpty())
    assertEquals(1, detector.calls)
    policy.vad(false, 8500)
    policy.tick(19899, 9999); assertTrue(out.isEmpty())
    policy.tick(19900, 10000)
    assertArrayEquals(loud + loud + loud + loud + loud + ByteArray(250 * soft.size).also { audio ->
      repeat(250) { soft.copyInto(audio, it * soft.size) }
    }, out.single().chunk.pcm)
    assertEquals(WakeEnd.SILENCE, out.single().reason)
  }
  @Test fun speechResumptionWithoutPcmStillExpiresAsUnknownTransportLoss() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    policy.vad(false, 120); policy.tick(13000, 3100)
    policy.vad(true, 3500)
    policy.tick(16399, 6499); assertTrue(policy.capturing)
    policy.tick(16400, 6500); assertFalse(policy.capturing); assertTrue(out.isEmpty())
  }
  @Test fun shortWakeTailOrCueDoesNotConsumeTheLongPauseAllowance() {
    val detector = Detector().apply { fire = true }; val out = mutableListOf<WakeCapture>()
    val policy = GlassesWakePolicy(detector, {}, out::add, {})
    policy.vad(true, 100); policy.pcm(tone(2000), 10000, 100, 0)
    repeat(6) { policy.pcm(tone(2000), 10020 + it * 20L, 120 + it * 20L, 0) }
    policy.vad(false, 240)
    policy.tick(13000, 3100); assertTrue(policy.capturing)
    policy.tick(14139, 4239); assertTrue(out.isEmpty())
    policy.tick(14140, 4240); assertEquals(7 * 640, out.single().chunk.pcm.size)
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
    policy.tick(14020, 4120)
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
    policy.vad(false, 120); policy.tick(14020, 4120)
    val resets = detector.resets
    repeat(100) { policy.tick(14500 + it * 1000L, 4600 + it * 1000L) }
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
