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
    locations.forEach { location ->
      if (!location.latitude.isFinite() || !location.longitude.isFinite() || location.time <= 0) return@forEach
      val id = "${location.time}:${location.latitude}:${location.longitude}"
      if (!knownIds.add(id)) return@forEach
      combined += JSONObject().put("id", id).put("latitude", location.latitude)
        .put("longitude", location.longitude).put("timestamp", location.time)
        .put("accuracy", if (location.hasAccuracy()) location.accuracy.toDouble() else JSONObject.NULL)
        .put("timezone", timezone)
      appended++
    }
    if (appended == 0) return

    val dropped = (combined.size - MAX_SAMPLES).coerceAtLeast(0)
    val next = JSONArray()
    combined.drop(dropped).forEach { next.put(it) }
    write(context, next)
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
    for (index in 0 until previous.length()) {
      val item = previous.getJSONObject(index)
      if (item.getString("id") !in ids) next.put(item)
    }
    write(context, next)
  }
}

data class RuntimeLocationSample(
  val id: String,
  val latitude: Double,
  val longitude: Double,
  val timestamp: Long,
  val accuracy: Double?,
  val timezone: String,
)
