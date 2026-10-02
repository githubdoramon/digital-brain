package expo.modules.digitalbrainruntime

/** Delays follow completed failed attempts, and never reset just because BLE briefly connects. */
class GlassesRetryPolicy {
  var attempt = 0
    private set
  fun nextDelayMs(): Long {
    val delays = longArrayOf(2_000, 5_000, 10_000, 20_000, 40_000, 60_000, 120_000, 300_000)
    val delay = delays[attempt.coerceAtMost(delays.lastIndex)]
    if (attempt < Int.MAX_VALUE) attempt++
    return delay
  }
  fun reset() { attempt = 0 }
}
