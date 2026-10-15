package expo.modules.digitalbrainruntime

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

internal enum class LedSource { WAKE, COMMAND, TEST_DIRECT, TEST_STANDARD, TEST_LEGACY, TEST_LONG, TEST_WHITE, TEST_CUSTOM }
internal enum class LedResult { REQUESTED, QUEUED, UNAVAILABLE, DISPATCH_FAILED, RESPONSE_SUCCESS, RESPONSE_ERROR, RESPONSE_TIMEOUT, VISIBLE, NOT_VISIBLE, SUPPRESSED_FOR_TEST }

/** Persistent bounded content-free LED trail. Queued/response success never implies visible light. */
internal object GlassesLedDiagnostics {
  private const val MAX_EVENTS = 150
  private fun prefs(c: Context) = c.getSharedPreferences("glasses_led_diagnostics", Context.MODE_PRIVATE)
  fun start(c: Context, source: LedSource, color: String, pattern: GlassesLightPattern): Int {
    val pref = prefs(c)
    val attempt = pref.getInt("sequence", 0).let { if (it == Int.MAX_VALUE) 1 else it + 1 }
    pref.edit().putInt("sequence", attempt).apply()
    record(c, attempt, source, LedResult.REQUESTED, color = color, pattern = pattern)
    return attempt
  }
  fun record(c: Context, attempt: Int, source: LedSource, result: LedResult, elapsed: Long = 0, color: String? = null, pattern: GlassesLightPattern? = null) {
    val pref = prefs(c)
    val old = runCatching { JSONArray(pref.getString("events", "[]")) }.getOrDefault(JSONArray())
    val next = JSONArray()
    for (i in maxOf(0, old.length() - (MAX_EVENTS - 1)) until old.length()) next.put(old.getJSONObject(i))
    val entry = JSONObject().put("atMs", System.currentTimeMillis()).put("attempt", attempt)
      .put("source", source.name).put("result", result.name).put("elapsedMs", elapsed)
    if (color != null && pattern != null) {
      entry.put("color", color).put("pattern", pattern.key).put("onMs", pattern.onMs)
        .put("offMs", pattern.offMs).put("count", pattern.count)
    } else {
      val previous = (old.length() - 1 downTo 0).map { old.optJSONObject(it) }
        .firstOrNull { it?.optInt("attempt") == attempt }
      if (previous != null) for (key in listOf("color", "pattern", "onMs", "offMs", "count")) {
        if (previous.has(key)) entry.put(key, previous.get(key))
      }
    }
    next.put(entry)
    pref.edit().putString("events", next.toString()).apply()
  }
  fun observation(c: Context, attempt: Int, visible: Boolean) {
    val source = events(c).lastOrNull { it["attempt"] == attempt }?.get("source") as? String
    val parsed = LedSource.entries.firstOrNull { it.name == source && it !in setOf(LedSource.WAKE, LedSource.COMMAND) }
    check(parsed != null) { "Run a light test first" }
    record(c, attempt, parsed, if (visible) LedResult.VISIBLE else LedResult.NOT_VISIBLE)
  }
  fun events(c: Context): List<Map<String, Any>> {
    val data = runCatching { JSONArray(prefs(c).getString("events", "[]")) }.getOrDefault(JSONArray())
    return (maxOf(0, data.length() - MAX_EVENTS) until data.length()).mapNotNull { i ->
      val event = data.optJSONObject(i) ?: return@mapNotNull null
      val source = LedSource.entries.firstOrNull { it.name == event.optString("source") } ?: return@mapNotNull null
      val result = LedResult.entries.firstOrNull { it.name == event.optString("result") } ?: return@mapNotNull null
      val safe = mutableMapOf<String, Any>("atMs" to event.optLong("atMs"), "attempt" to event.optInt("attempt"),
        "source" to source.name, "result" to result.name, "elapsedMs" to event.optLong("elapsedMs"))
      val color = com.mentra.bluetoothsdk.RgbLedColor.entries.firstOrNull { it.value == event.optString("color") }
      val pattern = GlassesLightPattern.entries.firstOrNull { it.key == event.optString("pattern") }
      if (color != null && pattern != null) safe.putAll(mapOf("color" to color.value, "pattern" to pattern.key,
        "onMs" to pattern.onMs, "offMs" to pattern.offMs, "count" to pattern.count))
      safe
    }
  }
}
