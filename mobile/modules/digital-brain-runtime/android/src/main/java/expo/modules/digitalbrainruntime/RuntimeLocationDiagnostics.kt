package expo.modules.digitalbrainruntime

import android.content.Context
import android.util.Log
import androidx.work.WorkManager
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Bounded native capture/upload trail. Never records coordinates, credentials, or request bodies. */
object RuntimeLocationDiagnostics {
  private const val PREFS = "digital_brain_runtime_location_diagnostics"
  private const val EVENTS = "events"
  private const val SEQUENCE = "sequence"
  private const val DROPPED_COUNT = "dropped_count"
  private const val MAX_EVENTS = 250
  private const val MAX_WORK_ITEMS = 20
  private const val WORK_INFO_TIMEOUT_MS = 750L

  private val allowedFields = setOf(
    "reason", "phase", "sample_key", "sample_keys", "sample_count", "sample_index", "sample_total",
    "dropped_sample_keys", "valid_count", "invalid_count", "duplicate_count", "added_count", "dropped_count",
    "queue_before", "queue_after", "queue_size", "work_id", "worker_id", "attempt", "run_attempt",
    "owner_enabled", "config_available", "auth_available", "retry", "result", "http_status",
    "wants_location", "fine_permission", "coarse_permission", "background_permission",
    "location_services_enabled", "foreground_types",
    "constraint", "existing_policy",
    "http_success", "content_type", "response_bytes", "duration_ms", "request_duration_ms",
    "validation_issues",
    "error_type", "state", "state_counts", "work_items", "captured_at", "capture_age_ms",
    "accuracy_m", "accuracy_mode", "captured_at_first", "captured_at_last", "interval_ms",
    "max_batch_delay_ms", "operation_state", "request_id",
    "lane", "movement_distance_m", "candidate_count", "captured_count", "time_triggered_count",
    "movement_triggered_count", "timer_reset", "capture_policy",
    "api_host_fingerprint", "api_endpoint", "retry_backoff_ms", "queue_read_error_type", "store_error_type",
    "uploaded_count",
  )

  @Synchronized fun record(context: Context, event: String, fields: Map<String, Any?> = emptyMap()) {
    try {
      val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      val events = runCatching { JSONArray(prefs.getString(EVENTS, "[]") ?: "[]") }
        .getOrElse { JSONArray() }
      val sequence = maxOf(prefs.getLong(SEQUENCE, 0L), events.lastSequence()) + 1
      val item = JSONObject()
        .put("sequence", sequence)
        .put("atMs", System.currentTimeMillis())
        .put("event", event)
      fields.forEach { (key, value) ->
        if (key in allowedFields) item.put(key, jsonValue(value))
      }
      val next = JSONArray()
      val firstRetainedIndex = (events.length() - MAX_EVENTS + 1).coerceAtLeast(0)
      for (index in firstRetainedIndex until events.length()) next.put(events.getJSONObject(index))
      next.put(item)
      val dropped = (events.length() + 1 - MAX_EVENTS).coerceAtLeast(0)
      prefs.edit()
        .putLong(SEQUENCE, sequence)
        .putLong(DROPPED_COUNT, prefs.getLong(DROPPED_COUNT, 0L) + dropped)
        .putString(EVENTS, next.toString())
        .apply()
      Log.i("DigitalBrainRuntime", "location_diag event=$event fields=${safeLogFields(item)}")
    } catch (error: Exception) {
      Log.w("DigitalBrainRuntime", "location_diag_persist_failed type=${error.javaClass.simpleName}")
    }
  }

  fun recentEvents(context: Context): List<Map<String, Any?>> {
    return runCatching {
      val events = JSONArray(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(EVENTS, "[]") ?: "[]")
      (0 until events.length()).map { index -> events.getJSONObject(index).toMap() }
    }.getOrDefault(emptyList())
  }

  fun historySnapshot(context: Context): Map<String, Any?> {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val events = runCatching { JSONArray(prefs.getString(EVENTS, "[]") ?: "[]") }
      .getOrElse { JSONArray() }
    return mapOf(
      "retainedCount" to events.length(),
      "retainedLimit" to MAX_EVENTS,
      "droppedTotal" to prefs.getLong(DROPPED_COUNT, 0L),
      "firstSequence" to events.firstSequenceOrNull(),
      "lastSequence" to events.lastSequenceOrNull(),
    )
  }

  fun workManagerSnapshot(context: Context): Map<String, Any?> {
    return try {
      val infos = WorkManager.getInstance(context.applicationContext)
        .getWorkInfosForUniqueWork(RuntimeLocationUploadScheduler.UNIQUE_WORK_NAME)
        .get(WORK_INFO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
      val items = infos.takeLast(MAX_WORK_ITEMS).map { info ->
        mapOf(
          "id" to info.id.toString(),
          "state" to info.state.name,
          "runAttemptCount" to info.runAttemptCount,
          "tags" to info.tags.filter { it == "digital-brain-location-upload" },
        )
      }
      val counts = infos.groupingBy { it.state.name }.eachCount()
      mapOf(
        "available" to true,
        "workCount" to infos.size,
        "stateCounts" to counts,
        "items" to items,
        "truncated" to (infos.size > MAX_WORK_ITEMS),
      )
    } catch (error: Exception) {
      mapOf("available" to false, "errorType" to error.javaClass.simpleName)
    }
  }

  fun queueSnapshot(context: Context): Map<String, Any?> {
    return try {
      val samples = RuntimeLocationStore.pendingSamples(context)
      mapOf(
        "available" to true,
        "count" to samples.size,
        "oldestCapturedAt" to samples.minOfOrNull { it.timestamp }?.let(::isoTimestamp),
        "newestCapturedAt" to samples.maxOfOrNull { it.timestamp }?.let(::isoTimestamp),
        "sampleKeys" to samples.map { RuntimeLocationDebugId.requestId(it.id) },
      )
    } catch (error: Exception) {
      mapOf("available" to false, "errorType" to error.javaClass.simpleName)
    }
  }

  private fun jsonValue(value: Any?): Any = when (value) {
    null -> JSONObject.NULL
    is String, is Number, is Boolean, is JSONObject, is JSONArray -> value
    is List<*> -> JSONArray().also { array -> value.forEach { array.put(jsonValue(it)) } }
    is Map<*, *> -> JSONObject().also { obj ->
      value.forEach { (key, nested) -> if (key is String) obj.put(key, jsonValue(nested)) }
    }
    else -> value.toString().take(128)
  }

  private fun JSONObject.toMap(): Map<String, Any?> = keys().asSequence().associateWith { key ->
    when (val value = get(key)) {
      JSONObject.NULL -> null
      is JSONObject -> value.toMap()
      is JSONArray -> (0 until value.length()).map { index ->
        when (val nested = value.get(index)) {
          JSONObject.NULL -> null
          is JSONObject -> nested.toMap()
          is JSONArray -> nested.toString()
          else -> nested
        }
      }
      else -> value
    }
  }

  private fun JSONArray.lastSequence(): Long = lastSequenceOrNull() ?: 0L

  private fun JSONArray.lastSequenceOrNull(): Long? =
    if (length() == 0) null else getJSONObject(length() - 1).optLong("sequence")

  private fun JSONArray.firstSequenceOrNull(): Long? =
    if (length() == 0) null else getJSONObject(0).optLong("sequence")

  private fun safeLogFields(item: JSONObject): String = runCatching {
    val fields = JSONObject()
    item.keys().forEach { key -> if (key != "atMs" && key != "event") fields.put(key, item.get(key)) }
    fields.toString()
  }.getOrDefault("{}")

  private fun isoTimestamp(timestamp: Long): String = java.text.SimpleDateFormat(
    "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
    java.util.Locale.US,
  ).apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date(timestamp))
}

object RuntimeLocationDebugId {
  fun forSample(id: String): String = java.security.MessageDigest.getInstance("SHA-256")
    .digest(id.toByteArray(Charsets.UTF_8))
    .take(10)
    .joinToString("") { "%02x".format(it) }

  fun requestId(id: String): String = "android-native-${forSample(id)}"
}
