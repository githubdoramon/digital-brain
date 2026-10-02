package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesAlertPolicyTest {
  @Test fun reusedMessageNotificationIdsCanChimeWithoutReadingMessageText() {
    assertTrue(GlassesAlertPolicy.isNewPost(null, 100, false))
    assertFalse(GlassesAlertPolicy.isNewPost(100, 100, false))
    assertTrue(GlassesAlertPolicy.isNewPost(100, 200, false))
    assertFalse(GlassesAlertPolicy.isNewPost(100, 200, true))
  }
  @Test fun chimesRespectSelectionPhoneUseCooldownAndCallPriority() {
    fun allows(selected: Boolean = true, fresh: Boolean = true, ongoing: Boolean = false,
      summary: Boolean = false, inUse: Boolean = false, ringing: Boolean = false, now: Long = 5_000,
      last: Long? = 0) = GlassesAlertPolicy.shouldChime(selected, fresh, ongoing, summary, inUse, ringing, now, last)
    assertTrue(allows())
    assertTrue(allows(now = 0, last = null))
    assertFalse(allows(now = 4_999))
    assertFalse(allows(selected = false))
    assertFalse(allows(fresh = false))
    assertFalse(allows(ongoing = true))
    assertFalse(allows(summary = true))
    assertFalse(allows(inUse = true))
    assertFalse(allows(ringing = true))
  }
  @Test fun ongoingScreeningAndMissedCallsDoNotRingEvenWithFullScreenIntent() {
    assertEquals(GlassesCallKind.INCOMING, GlassesAlertPolicy.callKind(true, 1, false))
    assertEquals(GlassesCallKind.INCOMING, GlassesAlertPolicy.callKind(true, 0, true))
    assertEquals(GlassesCallKind.OTHER_CALL, GlassesAlertPolicy.callKind(true, 2, true))
    assertEquals(GlassesCallKind.OTHER_CALL, GlassesAlertPolicy.callKind(true, 3, true))
    assertEquals(GlassesCallKind.OTHER_CALL, GlassesAlertPolicy.callKind(true, 0, false))
    assertEquals(GlassesCallKind.NONE, GlassesAlertPolicy.callKind(false, 0, false))
  }
  @Test fun endingOneCallDoesNotStopAnotherAndRepeatedPostsDoNotExtendRingForever() {
    val calls = GlassesCallSources()
    calls.incoming("cellular", 0)
    calls.incoming("example.app:call", 1_000)
    calls.incoming("example.app:call", 100_000)
    assertEquals(120_000L, calls.nextExpiry())
    calls.ended("cellular")
    assertTrue(calls.active())
    assertEquals(121_000L, calls.nextExpiry())
    calls.expire(121_000)
    assertFalse(calls.active())
    calls.incoming("example.app:call", 122_000)
    assertFalse(calls.active())
    calls.ended("example.app:call")
    calls.incoming("example.app:call", 123_000)
    assertTrue(calls.active())
    calls.clear()
    assertFalse(calls.active())
    assertNull(calls.nextExpiry())
  }
}
