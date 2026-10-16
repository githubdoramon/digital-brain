package expo.modules.digitalbrainruntime

/** All durations share one monotonic clock. A failed stage retains time spent before failure. */
internal class SpeechTiming(private val clock: () -> Long, private val started: Long = clock()) {
  val values = linkedMapOf<String, Any?>()
  fun elapsed() = (clock() - started).coerceAtLeast(0)
  fun duration(key: String) = (values[key] as? Long) ?: 0L
  fun <T> measure(key: String, action: () -> T): T {
    val start = clock()
    try { return action() } finally { values[key] = (clock() - start).coerceAtLeast(0) }
  }
  fun snapshot(): Map<String, Any?> = values + mapOf("schemaVersion" to 1, "elapsedMs" to elapsed())
}
