package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesUploadReasonTest {
  @Test fun knownCompletionFailuresRemainDistinguishable() {
    assertEquals(GlassesUploadReason.ALBUM_NOT_CONFIGURED,
      GlassesUploadReason.fromDetail("Configure GLASSES_IMMICH_ALBUM_ID for the existing glasses album"))
    assertEquals(GlassesUploadReason.CHECKSUM, GlassesUploadReason.fromDetail("Original media checksum mismatch"))
    assertEquals(GlassesUploadReason.INCOMPLETE, GlassesUploadReason.fromDetail("Incomplete original media"))
  }
  @Test fun unknownResponseContentsNeverReachExceptionOrLogs() {
    val reason = GlassesUploadReason.fromDetail("example-private-session-or-network")
    assertEquals(GlassesUploadReason.UNKNOWN, reason)
    assertFalse(GlassesUploadException(409, reason).message!!.contains("example-private"))
  }
}
