package expo.modules.digitalbrainruntime

import android.content.Context
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/** Current runtime health snapshots; location event logging has been retired. */
object RuntimeLocationDiagnostics {
  private const val PREFS = "digital_brain_runtime_location_diagnostics"
  private const val MAX_WORK_ITEMS = 20
  private const val WORK_INFO_TIMEOUT_MS = 750L

  /** Remove the retired location event trail on upgrade. */
  fun clearLegacyHistory(context: Context) {
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    context.getSharedPreferences("digital_brain_runtime_upload_stats", Context.MODE_PRIVATE)
      .edit().remove("recent_runs").apply()
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
