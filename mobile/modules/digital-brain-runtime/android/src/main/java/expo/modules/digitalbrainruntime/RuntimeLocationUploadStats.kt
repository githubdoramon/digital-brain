package expo.modules.digitalbrainruntime

import android.content.Context

object RuntimeLocationUploadStats {
  private const val PREFS = "digital_brain_runtime_upload_stats"
  private const val RUN_COUNT = "run_count"
  private const val UPLOADED_COUNT = "uploaded_count"
  private const val LAST_DURATION_MS = "last_duration_ms"
  private const val LAST_OUTCOME = "last_outcome"
  private const val LAST_HTTP_STATUS = "last_http_status"
  private const val LAST_QUEUE_SIZE = "last_queue_size"

  @Synchronized fun record(
    context: Context,
    durationMs: Long,
    outcome: String,
    uploadedCount: Int,
    httpStatus: Int?,
    queueSize: Int,
  ) {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val editor = prefs.edit()
      .putLong(RUN_COUNT, prefs.getLong(RUN_COUNT, 0L) + 1)
      .putLong(UPLOADED_COUNT, prefs.getLong(UPLOADED_COUNT, 0L) + uploadedCount)
      .putLong(LAST_DURATION_MS, durationMs)
      .putString(LAST_OUTCOME, outcome)
      .putInt(LAST_QUEUE_SIZE, queueSize)
    if (httpStatus == null) editor.remove(LAST_HTTP_STATUS) else editor.putInt(LAST_HTTP_STATUS, httpStatus)
    editor.apply()
  }

  fun snapshot(context: Context): Map<String, Any?> {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    return mapOf(
      "nativeUploadRunCount" to prefs.getLong(RUN_COUNT, 0L),
      "nativeUploadSampleCount" to prefs.getLong(UPLOADED_COUNT, 0L),
      "nativeUploadLastDurationMs" to prefs.getLong(LAST_DURATION_MS, 0L),
      "nativeUploadLastOutcome" to (prefs.getString(LAST_OUTCOME, "none") ?: "none"),
      "nativeUploadLastHttpStatus" to if (prefs.contains(LAST_HTTP_STATUS)) prefs.getInt(LAST_HTTP_STATUS, 0) else null,
      "nativeUploadLastQueueSize" to prefs.getInt(LAST_QUEUE_SIZE, 0),
    )
  }
}
