package expo.modules.digitalbrainruntime

/** Bound headless JavaScript wake-ups while location capture is active. */
class RuntimeWorkCadence {
  private var lastRequestAtMs: Long? = null

  fun shouldRequest(nowMs: Long, owners: Set<RuntimeFeature>): Boolean {
    if (RuntimeFeature.LOCATION !in owners) return false
    val interval = 900_000L
    return lastRequestAtMs?.let { nowMs - it >= interval } ?: true
  }

  fun requested(nowMs: Long) { lastRequestAtMs = nowMs }
}
