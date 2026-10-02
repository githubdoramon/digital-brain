package expo.modules.digitalbrainruntime

import android.content.Context
import android.location.Location
import android.util.AtomicFile
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.util.TimeZone

/** Native capture commits before upload; ACK follows a successful native HTTP response. */
object RuntimeLocationStore {
  private const val MAX_SAMPLES = 200
  private fun file(context: Context) = AtomicFile(File(context.filesDir, "runtime-locations.json"))
  private fun read(context: Context): JSONArray {
    val store = file(context)
    return try {
      // readFully also recovers AtomicFile's backup after an interrupted write.
      JSONArray(String(store.readFully(), Charsets.UTF_8))
    } catch (error: FileNotFoundException) {
      if (store.baseFile.exists() || File(store.baseFile.path + ".bak").exists()) throw error
      JSONArray()
    }
  }
  private fun write(context: Context, samples: JSONArray) {
    val store = file(context)
    val stream = store.startWrite()
    try {
      stream.write(samples.toString().toByteArray(Charsets.UTF_8))
      store.finishWrite(stream)
    } catch (error: Exception) {
      store.failWrite(stream)
      throw error
    }
  }
  @Synchronized fun appendBatch(context: Context, locations: List<Location>) {
    val previous = read(context)
    val knownIds = (0 until previous.length()).mapTo(mutableSetOf()) {
      previous.getJSONObject(it).getString("id")
    }
    val combined = (0 until previous.length()).map { previous.getJSONObject(it) }.toMutableList()
    val timezone = TimeZone.getDefault().id
    var appended = 0
    var invalid = 0
    var duplicate = 0
    val addedIds = mutableListOf<String>()
    locations.forEach { location ->
      if (!location.latitude.isFinite() || !location.longitude.isFinite() || location.time <= 0) {
        invalid++
        return@forEach
      }
      val id = "${location.time}:${location.latitude}:${location.longitude}"
      if (!knownIds.add(id)) {
        duplicate++
        return@forEach
      }
      combined += JSONObject().put("id", id).put("latitude", location.latitude)
        .put("longitude", location.longitude).put("timestamp", location.time)
        .put("accuracy", if (location.hasAccuracy()) location.accuracy.toDouble() else JSONObject.NULL)
        .put("timezone", timezone)
      addedIds += id
      appended++
    }
    if (appended == 0) {
      RuntimeLocationDiagnostics.record(context, "capture_batch_no_new_samples", mapOf(
        "sample_count" to locations.size,
        "invalid_count" to invalid,
        "duplicate_count" to duplicate,
        "queue_before" to previous.length(),
        "queue_after" to previous.length(),
      ))
      return
    }

    val dropped = (combined.size - MAX_SAMPLES).coerceAtLeast(0)
    val next = JSONArray()
    combined.drop(dropped).forEach { next.put(it) }
    write(context, next)
    val retainedIds = (0 until next.length()).mapTo(mutableSetOf()) { next.getJSONObject(it).getString("id") }
    val retainedSampleKeys = addedIds.filter { it in retainedIds }.map { RuntimeLocationDebugId.requestId(it) }
    val droppedSampleKeys = combined.take(dropped).map { item ->
      RuntimeLocationDebugId.requestId(item.getString("id"))
    }
    RuntimeLocationDiagnostics.record(context, "capture_batch_persisted", mapOf(
      "sample_count" to locations.size,
      "valid_count" to (locations.size - invalid),
      "invalid_count" to invalid,
      "duplicate_count" to duplicate,
      "added_count" to appended,
      "dropped_count" to dropped,
      "queue_before" to previous.length(),
      "queue_after" to next.length(),
      "sample_keys" to retainedSampleKeys,
      "dropped_sample_keys" to droppedSampleKeys,
      "captured_at_first" to locations.filter { it.time > 0 }.minOfOrNull { it.time }?.let(::isoTimestamp),
      "captured_at_last" to locations.filter { it.time > 0 }.maxOfOrNull { it.time }?.let(::isoTimestamp),
    ))
    Log.i("DigitalBrainRuntime", "location_enqueued added=$appended count=${next.length()} dropped=$dropped")
  }
  @Synchronized fun pendingSamples(context: Context): List<RuntimeLocationSample> {
    val samples = read(context)
    return (0 until samples.length()).map { index ->
      val item = samples.getJSONObject(index)
      RuntimeLocationSample(
        id = item.getString("id"),
        latitude = item.getDouble("latitude"),
        longitude = item.getDouble("longitude"),
        timestamp = item.getLong("timestamp"),
        accuracy = if (item.isNull("accuracy")) null else item.getDouble("accuracy"),
        timezone = item.getString("timezone"),
      )
    }
  }

  @Synchronized fun acknowledge(context: Context, ids: Set<String>) {
    val previous = read(context)
    val next = JSONArray()
    val acknowledgedKeys = mutableListOf<String>()
    for (index in 0 until previous.length()) {
      val item = previous.getJSONObject(index)
      val id = item.getString("id")
      if (id in ids) {
        acknowledgedKeys += RuntimeLocationDebugId.requestId(id)
      } else {
        next.put(item)
      }
    }
    write(context, next)
    RuntimeLocationDiagnostics.record(context, "samples_acknowledged", mapOf(
      "sample_keys" to acknowledgedKeys,
      "sample_count" to acknowledgedKeys.size,
      "queue_before" to previous.length(),
      "queue_after" to next.length(),
    ))
  }

  private fun isoTimestamp(timestamp: Long): String = java.text.SimpleDateFormat(
    "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
    java.util.Locale.US,
  ).apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date(timestamp))
}

data class RuntimeLocationSample(
  val id: String,
  val latitude: Double,
  val longitude: Double,
  val timestamp: Long,
  val accuracy: Double?,
  val timezone: String,
)
