package expo.modules.digitalbrainglassesalerts

/** Elapsed-time gate: minute capture opportunities must not imply minute JS wake locks. */
class RuntimeWorkCadence {
  private var lastRequestAtMs: Long? = null

  fun shouldRequest(nowMs: Long, owners: Set<RuntimeFeature>): Boolean {
    val interval = when {
      RuntimeFeature.GLASSES in owners -> 300_000L
      RuntimeFeature.LOCATION in owners -> 900_000L
      else -> return false
    }
    return lastRequestAtMs?.let { nowMs - it >= interval } ?: true
  }

  fun requested(nowMs: Long) { lastRequestAtMs = nowMs }
}
