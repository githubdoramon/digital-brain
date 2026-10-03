package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesRecordingPolicyTest {
  @Test fun audioClockUsesSamplesRatherThanCallbackTimingAndHandlesLongSessions() {
    assertEquals(20_000L, GlassesRecordingPolicy.presentationUs(640))
    assertEquals(12L * 3600 * 1_000_000, GlassesRecordingPolicy.presentationUs(12L * 3600 * 32000))
    assertEquals(62L, GlassesRecordingPolicy.presentationUs(2))
  }
  @Test fun rejectsMisinterpretedAudioAndUnboundedFrames() {
    assertTrue(GlassesRecordingPolicy.validPcm(16000, 16, 1, "pcm_s16le", 640))
    for (bytes in listOf(0, 1, 639, 65538)) assertFalse(GlassesRecordingPolicy.validPcm(16000, 16, 1, "pcm_s16le", bytes))
    assertFalse(GlassesRecordingPolicy.validPcm(48000, 16, 1, "pcm_s16le", 640))
    assertFalse(GlassesRecordingPolicy.validPcm(16000, 16, 2, "pcm_s16le", 640))
    assertFalse(GlassesRecordingPolicy.validPcm(16000, 32, 1, "pcm_f32le", 640))
  }
  @Test fun missedCallsDoNotStopRecordingButIncomingAndActiveCallsDo() {
    assertFalse(GlassesRecordingPolicy.callInterrupts(false, 0, true, false))
    assertFalse(GlassesRecordingPolicy.callInterrupts(false, 0, false, true))
    assertTrue(GlassesRecordingPolicy.callInterrupts(true, 1, true, false))
    assertTrue(GlassesRecordingPolicy.callInterrupts(false, 2, true, true))
    assertTrue(GlassesRecordingPolicy.callInterrupts(false, 3, true, false))
    assertTrue(GlassesRecordingPolicy.callInterrupts(false, 0, true, true))
  }
}
