package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test

class GlassesRetryPolicyTest {
  @Test fun unavailableOvernightKeepsFiveMinuteCapWithoutRestartingFastCycle() {
    val policy = GlassesRetryPolicy()
    val initial = (1..8).map { policy.nextDelayMs() }
    assertEquals(listOf(2_000L, 5_000L, 10_000L, 20_000L, 40_000L, 60_000L, 120_000L, 300_000L), initial)
    repeat(200) { assertEquals(300_000L, policy.nextDelayMs()) }
    assertEquals(208, policy.attempt)
  }
  @Test fun stableConnectionRestoresQuickRecovery() {
    val policy = GlassesRetryPolicy()
    repeat(12) { policy.nextDelayMs() }
    policy.reset()
    assertEquals(0, policy.attempt)
    assertEquals(2_000L, policy.nextDelayMs())
  }
}
