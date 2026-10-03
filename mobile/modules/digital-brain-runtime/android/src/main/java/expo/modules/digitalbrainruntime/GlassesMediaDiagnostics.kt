package expo.modules.digitalbrainruntime

import android.content.Context
import androidx.work.WorkManager
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Persistent bounded metadata only. No arbitrary messages, addresses, media names or bodies. */
object GlassesMediaDiagnostics {
  const val LIMIT = 250
  private const val PREFS = "glasses_media_diagnostics"

  @Synchronized fun record(
    c: Context, event: GlassesMediaEvent, phase: GlassesMediaPhase? = null,
    transport: GlassesMediaTransport? = null, path: String? = null,
    httpStatus: Int? = null, error: GlassesMediaFailure? = null,
    count: Long? = null, durationMs: Long? = null, attempt: Int? = null,
    uploadReason: GlassesUploadReason? = null,
  ) {
    // Diagnostics must never interrupt a transfer, including when private storage is unavailable.
    runCatching {
      val prefs = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      val events = JSONArray(prefs.getString("events", "[]"))
      val sequence = prefs.getLong("sequence", 0) + 1
      val item = JSONObject().put("sequence", sequence).put("atMs", System.currentTimeMillis())
        .put("event", event.name)
      phase?.let { item.put("phase", it.name) }
      transport?.let { item.put("transport", it.name) }
      path?.let { item.put("endpoint", GlassesMediaDiagnosticPolicy.endpoint(it)) }
      httpStatus?.let { item.put("httpStatus", it) }
      error?.let { item.put("error", it.name) }
      uploadReason?.let { item.put("uploadReason", it.name) }
      count?.let { item.put("count", it) }
      durationMs?.let { item.put("durationMs", it) }
      attempt?.let { item.put("attempt", it) }
      events.put(item)
      val next = JSONArray()
      for (i in maxOf(0, events.length() - LIMIT) until events.length()) next.put(events.getJSONObject(i))
      prefs.edit().putString("events", next.toString()).putLong("sequence", sequence)
        .putLong("dropped", prefs.getLong("dropped", 0) + maxOf(0, events.length() - LIMIT)).apply()
    }
  }

  /** Call on IO: the bounded WorkManager query must not block the UI thread. */
  @Synchronized fun snapshot(c: Context): Map<String, Any?> {
    val prefs = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val events = runCatching { JSONArray(prefs.getString("events", "[]")) }.getOrElse { JSONArray() }
    val work = runCatching {
      WorkManager.getInstance(c).getWorkInfosForUniqueWork("glasses-media-sync")
        .get(750, TimeUnit.MILLISECONDS).takeLast(20).map {
          mapOf("state" to it.state.name, "attempt" to it.runAttemptCount)
        }
    }
    return mapOf(
      "schemaVersion" to 1, "retainedLimit" to LIMIT, "droppedTotal" to prefs.getLong("dropped", 0),
      "events" to (0 until events.length()).map { i ->
        val item = events.getJSONObject(i)
        item.keys().asSequence().associateWith { key -> item.get(key) }
      },
      "work" to work.getOrDefault(emptyList()), "workQueryAvailable" to work.isSuccess,
    )
  }
}

enum class GlassesMediaEvent {
  ENQUEUED, CANCEL_REQUESTED, RUN_STARTED, RUN_SUCCESS, RUN_RETRY, RUN_FAILED, RUN_CANCELLED,
  PHASE, TRANSPORT, GALLERY_HTTP, REQUEST_FAILED, UPLOAD_HTTP, UPLOAD_CONFIRMED,
  LAN_GALLERY_ENABLED, LAN_GALLERY_UNAVAILABLE,
  DOWNLOAD_COMPLETE, GALLERY_COUNT, CAPTURE_CLEANED, WAITING_FOR_GLASSES, CAMERA_BUSY,
}
enum class GlassesMediaFailure {
  HTTP, TIMEOUT, NETWORK, DISCONNECTED, INVALID_RESPONSE, REJECTED, MALFORMED_MEDIA,
  PERMISSION, AUTH, OTHER, CONNECTION_REFUSED, NO_ROUTE, CLEARTEXT_BLOCKED,
}
