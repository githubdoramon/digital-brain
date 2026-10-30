package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CancellationException

class GlassesMediaUploadBatchTest {
  @Test fun rejectionAllowsDiscoveryAndOtherUploadsBeforeRetry() {
    val batch = GlassesMediaUploadBatch {}
    val rejection = GlassesUploadException(409, GlassesUploadReason.ASSET_UNREADABLE)
    val events = mutableListOf<String>()
    assertFalse(batch.attempt("cached") { events.add("cached"); throw rejection })
    events.add("discover")
    assertFalse(batch.attempt("cached") { fail("Failed capture retried in same run") })
    assertTrue(batch.attempt("new") { events.add("new") })
    assertEquals(listOf("cached", "discover", "new"), events)
    try { batch.throwIfFailed(); fail("Failed original must schedule retry") }
    catch (e: GlassesUploadException) { assertSame(rejection, e) }
  }

  @Test fun newRunRetriesFailedOriginal() {
    assertFalse(GlassesMediaUploadBatch {}.attempt("cached") { throw java.io.IOException() })
    val retry = GlassesMediaUploadBatch {}
    assertTrue(retry.attempt("cached") {})
    retry.throwIfFailed()
  }

  @Test fun cancellationStopsBatchImmediately() {
    val cancellation = CancellationException()
    try {
      GlassesMediaUploadBatch {}.attempt("cached") { throw cancellation }
      fail("Cancellation swallowed")
    } catch (e: CancellationException) { assertSame(cancellation, e) }
  }

  @Test fun lostEligibilityOrDeadlineStopsBatchAfterFailure() {
    var active = true
    val batch = GlassesMediaUploadBatch { check(active) { "Stopped" } }
    try {
      batch.attempt("cached") { active = false; throw java.io.IOException() }
      fail("Inactive work continued")
    } catch (e: IllegalStateException) { assertEquals("Stopped", e.message) }
  }
}
