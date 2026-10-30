package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesSpeechPlaybackPolicyTest {
  @Test fun elapsedReplyDurationDoesNotProveFramesPlayed() {
    assertFalse(GlassesSpeechPlaybackPolicy.complete(24_000 * 15, 0, true))
    assertFalse(GlassesSpeechPlaybackPolicy.complete(24_000 * 15, 24_000, true))
    assertTrue(GlassesSpeechPlaybackPolicy.complete(24_000 * 15, 24_000L * 15, true))
  }
  @Test fun fullPlaybackOnAnUnverifiedRouteIsNotSuccess() {
    assertFalse(GlassesSpeechPlaybackPolicy.complete(24_000, 24_000, false))
    assertFalse(GlassesSpeechPlaybackPolicy.complete(0, 0, true))
  }
  @Test fun diagnosticsDistinguishSilenceAndSignedLittleEndianPcmWithoutRetainingAudio() {
    assertEquals(0, GlassesSpeechPlaybackPolicy.peak(ByteArray(200)))
    assertEquals(32768, GlassesSpeechPlaybackPolicy.peak(byteArrayOf(0, -128)))
    assertEquals(32767, GlassesSpeechPlaybackPolicy.peak(byteArrayOf(-1, 127)))
    assertEquals(256, GlassesSpeechPlaybackPolicy.peak(byteArrayOf(0, 1, -1, -1)))
  }
}
