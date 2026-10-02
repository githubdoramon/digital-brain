package expo.modules.digitalbrainruntime

/** Content-free decisions shared by native callbacks and JVM regression tests. */
internal enum class GlassesCallKind { NONE, INCOMING, OTHER_CALL }

internal object GlassesAlertPolicy {
  const val CHIME_COOLDOWN_MS = 5_000L
  const val CALL_TIMEOUT_MS = 120_000L

  fun callKind(categoryCall: Boolean, callType: Int, fullScreen: Boolean): GlassesCallKind = when {
    callType == 1 -> GlassesCallKind.INCOMING // Android/AndroidX CallStyle incoming.
    callType == 2 || callType == 3 -> GlassesCallKind.OTHER_CALL
    categoryCall && fullScreen -> GlassesCallKind.INCOMING // Legacy call apps.
    categoryCall -> GlassesCallKind.OTHER_CALL // Never ring ongoing or missed-call notifications.
    else -> GlassesCallKind.NONE
  }

  fun isNewPost(previousTimestamp: Long?, timestamp: Long, onlyAlertOnce: Boolean): Boolean =
    previousTimestamp == null || (previousTimestamp != timestamp && !onlyAlertOnce)

  fun shouldChime(selected: Boolean, newPost: Boolean, ongoing: Boolean, summary: Boolean,
    phoneInUse: Boolean, ringing: Boolean, now: Long, lastChime: Long?): Boolean =
    selected && newPost && !ongoing && !summary && !phoneInUse && !ringing &&
      (lastChime == null || now - lastChime >= CHIME_COOLDOWN_MS)
}

/** A removal or incoming-to-ongoing update stops that source, not unrelated calls. */
internal class GlassesCallSources {
  private val sources = linkedMapOf<String, Long>()
  private val expired = linkedSetOf<String>()
  fun incoming(key: String, now: Long) {
    if (key !in sources && key !in expired && sources.size < 32) sources[key] = now
  }
  fun ended(key: String) { sources.remove(key); expired.remove(key) }
  fun clear() { sources.clear(); expired.clear() }
  fun expire(now: Long) {
    val stale = sources.filterValues { now - it >= GlassesAlertPolicy.CALL_TIMEOUT_MS }.keys
    stale.forEach { sources.remove(it); expired.add(it) }
    while (expired.size > 128) expired.remove(expired.first())
  }
  fun active() = sources.isNotEmpty()
  fun nextExpiry(): Long? = sources.values.minOrNull()?.plus(GlassesAlertPolicy.CALL_TIMEOUT_MS)
}
