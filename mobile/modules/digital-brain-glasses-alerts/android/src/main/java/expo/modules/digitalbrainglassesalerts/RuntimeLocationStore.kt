package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.location.Location
import android.util.AtomicFile
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.util.TimeZone

/** Native capture commits before any JS/auth/network work; ACK follows the JS durable commit. */
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
  @Synchronized fun append(context: Context, location: Location) {
    if (!location.latitude.isFinite() || !location.longitude.isFinite() || location.time <= 0) return
    val id = "${location.time}:${location.latitude}:${location.longitude}"
    val previous = read(context)
    if ((0 until previous.length()).any { previous.getJSONObject(it).getString("id") == id }) return
    val next = JSONArray()
    val dropped = (previous.length() + 1 - MAX_SAMPLES).coerceAtLeast(0)
    for (index in dropped until previous.length()) next.put(previous.getJSONObject(index))
    next.put(JSONObject().put("id", id).put("latitude", location.latitude)
      .put("longitude", location.longitude).put("timestamp", location.time)
      .put("accuracy", if (location.hasAccuracy()) location.accuracy.toDouble() else JSONObject.NULL)
      .put("timezone", TimeZone.getDefault().id))
    write(context, next)
    Log.i("DigitalBrainRuntime", "location_enqueued count=${next.length()} dropped=$dropped")
  }
  @Synchronized fun samples(context: Context): List<Map<String, Any?>> {
    val samples = read(context)
    return (0 until samples.length()).map { index ->
      val item = samples.getJSONObject(index)
      mapOf("id" to item.getString("id"), "latitude" to item.getDouble("latitude"),
        "longitude" to item.getDouble("longitude"), "timestamp" to item.getLong("timestamp"),
        "accuracy" to if (item.isNull("accuracy")) null else item.getDouble("accuracy"),
        "timezone" to item.getString("timezone"))
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
