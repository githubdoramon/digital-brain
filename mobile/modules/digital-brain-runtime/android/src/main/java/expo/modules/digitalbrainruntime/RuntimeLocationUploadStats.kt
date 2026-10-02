package expo.modules.digitalbrainruntime

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

object RuntimeLocationUploadStats {
  private const val PREFS = "digital_brain_runtime_upload_stats"
  private const val RUN_COUNT = "run_count"
  private const val UPLOADED_COUNT = "uploaded_count"
  private const val LAST_RUN_AT_MS = "last_run_at_ms"
  private const val LAST_DURATION_MS = "last_duration_ms"
  private const val LAST_OUTCOME = "last_outcome"
  private const val LAST_HTTP_STATUS = "last_http_status"
  private const val LAST_QUEUE_SIZE = "last_queue_size"
  private const val RECENT_RUNS = "recent_runs"
  private const val MAX_RECENT_RUNS = 30

  @Synchronized fun record(
    context: Context,
    durationMs: Long,
    outcome: String,
    uploadedCount: Int,
    httpStatus: Int?,
    queueSize: Int,
    processCpuMs: Long,
    deviceAwakeMs: Long,
    batteryChargeDeltaMicroAh: Long,
  ) {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val atMs = System.currentTimeMillis()
    val run = JSONObject()
      .put("atMs", atMs)
      .put("durationMs", durationMs)
      .put("uploaded", uploadedCount)
      .put("queued", queueSize)
      .put("outcome", outcome)
      .put("httpStatus", httpStatus ?: JSONObject.NULL)
      .put("processCpuMs", processCpuMs)
      .put("deviceAwakeMs", deviceAwakeMs)
      .put("batteryChargeDeltaMicroAh", batteryChargeDeltaMicroAh)
    val recentRuns = runCatching {
      JSONArray(prefs.getString(RECENT_RUNS, "[]") ?: "[]")
    }
      .getOrElse { JSONArray() }
    val boundedRuns = JSONArray()
    val firstRetainedIndex = (recentRuns.length() - MAX_RECENT_RUNS + 1).coerceAtLeast(0)
    for (index in firstRetainedIndex until recentRuns.length()) {
      boundedRuns.put(recentRuns.getJSONObject(index))
    }
    boundedRuns.put(run)
    val editor = prefs.edit()
      .putLong(RUN_COUNT, prefs.getLong(RUN_COUNT, 0L) + 1)
      .putLong(UPLOADED_COUNT, prefs.getLong(UPLOADED_COUNT, 0L) + uploadedCount)
      .putLong(LAST_RUN_AT_MS, atMs)
      .putLong(LAST_DURATION_MS, durationMs)
      .putString(LAST_OUTCOME, outcome)
      .putInt(LAST_QUEUE_SIZE, queueSize)
      .putString(RECENT_RUNS, boundedRuns.toString())
    if (httpStatus == null) {
      editor.remove(LAST_HTTP_STATUS)
    } else {
      editor.putInt(LAST_HTTP_STATUS, httpStatus)
    }
    editor.apply()
  }

  fun snapshot(context: Context): Map<String, Any?> {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    return mapOf(
      "nativeUploadRunCount" to prefs.getLong(RUN_COUNT, 0L),
      "nativeUploadSampleCount" to prefs.getLong(UPLOADED_COUNT, 0L),
      "nativeUploadLastRunAtMs" to if (prefs.contains(LAST_RUN_AT_MS)) {
        prefs.getLong(LAST_RUN_AT_MS, 0L)
      } else {
        null
      },
      "nativeUploadLastDurationMs" to prefs.getLong(LAST_DURATION_MS, 0L),
      "nativeUploadLastOutcome" to (prefs.getString(LAST_OUTCOME, "none") ?: "none"),
      "nativeUploadLastHttpStatus" to if (prefs.contains(LAST_HTTP_STATUS)) {
        prefs.getInt(LAST_HTTP_STATUS, 0)
      } else {
        null
      },
      "nativeUploadLastQueueSize" to prefs.getInt(LAST_QUEUE_SIZE, 0),
      "nativeUploadRecentRuns" to readRecentRuns(prefs.getString(RECENT_RUNS, "[]") ?: "[]"),
    )
  }

  private fun readRecentRuns(raw: String): List<Map<String, Any?>> {
    return runCatching {
      val runs = JSONArray(raw)
      (0 until runs.length()).map { index ->
        val run = runs.getJSONObject(index)
        mapOf(
          "atMs" to run.optLong("atMs"),
          "durationMs" to run.optLong("durationMs"),
          "uploaded" to run.optInt("uploaded"),
          "queued" to run.optInt("queued"),
          "outcome" to run.optString("outcome"),
          "httpStatus" to run.takeUnless { it.isNull("httpStatus") }?.optInt("httpStatus"),
          "processCpuMs" to run.optLong("processCpuMs"),
          "deviceAwakeMs" to run.optLong("deviceAwakeMs"),
          "batteryChargeDeltaMicroAh" to run.optLong("batteryChargeDeltaMicroAh"),
        )
      }
    }.getOrDefault(emptyList())
  }
}
