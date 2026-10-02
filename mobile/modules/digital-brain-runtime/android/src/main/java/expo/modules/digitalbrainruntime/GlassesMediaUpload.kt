package expo.modules.digitalbrainruntime

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.io.RandomAccessFile

/** One-MiB resumable uploads; no entire photo/video is held in memory. */
class GlassesMediaUpload(private val c: Context, private val owner: String) {
  private val config = checkNotNull(RuntimeLocationUploadConfigStore.load(c)) { "Upload configuration unavailable" }
  private var token: String? = null
  private fun eligible() = RuntimeGlasses.signedIn(c) && RuntimeGlasses.enabled(c) && GlassesMediaStore.owner(c) == owner
  fun request(path: String, body: ByteArray? = null, method: String = if (body == null) "GET" else "POST"): JSONObject {
    repeat(2) { attempt ->
      check(eligible()) { "Glasses sync paused" }
      if (token == null) token = RuntimeGoogleIdTokenProvider.getFreshIdToken(c, config.googleWebClientId)
      check(eligible() && token != null) { "Sign in to upload glasses media" }
      val connection = URL(config.apiBaseUrl.trimEnd('/') + "/mobile/glasses/media" + path).openConnection() as HttpURLConnection
      try {
        connection.connectTimeout = 15000; connection.readTimeout = 120000; connection.instanceFollowRedirects = false
        connection.requestMethod = method; connection.setRequestProperty("Authorization", "Bearer $token")
        if (body != null) {
          connection.doOutput = true; connection.setFixedLengthStreamingMode(body.size)
          connection.setRequestProperty("Content-Type", if (method == "PUT") "application/octet-stream" else "application/json")
          connection.outputStream.use { it.write(body) }
        }
        val status = connection.responseCode
        if (status == 401 && attempt == 0) { token = null; return@repeat }
        check(status in 200..299) { "Digital Brain upload HTTP $status" }
        return connection.inputStream.use { JSONObject(String(GlassesMediaStore.readBounded(it, 65536))) }
      } finally { connection.disconnect() }
    }
    error("Sign in again to resume uploads")
  }
  fun upload(record: JSONObject, file: JSONObject, deadline: Long, active: () -> Boolean) {
    val key = file.getString("key")
    if (request("/receipts/$key").optBoolean("confirmed")) { file.put("confirmed", true); return }
    val original = GlassesMediaStore.bytes(c, owner, key)
    val metadata = JSONObject().put("capture_key", key).put("sha256", file.getString("sha256"))
      .put("size", file.getLong("size")).put("filename", file.getString("name").substringAfterLast('/'))
      .put("mime_type", file.getString("mime"))
      .put("captured_at", if (record.has("timestamp")) java.time.Instant.ofEpochMilli(record.getLong("timestamp")).toString() else JSONObject.NULL)
    val session = request("/sessions", metadata.toString().toByteArray())
    if (session.optBoolean("confirmed")) { file.put("confirmed", true); return }
    val id = session.getString("session_id")
    var offset = session.getLong("offset")
    check(offset in 0..original.length()) { "Invalid upload offset" }
    RandomAccessFile(original, "r").use { input ->
      while (offset < input.length()) {
        check(active() && System.currentTimeMillis() < deadline && eligible()) { "Sync will continue shortly" }
        input.seek(offset)
        val bytes = ByteArray(minOf(1024 * 1024L, input.length() - offset).toInt()); input.readFully(bytes)
        val next = request("/sessions/$id?offset=$offset", bytes, "PUT").getLong("offset")
        check(next == offset + bytes.size) { "Invalid upload acknowledgement" }; offset = next
      }
    }
    check(request("/sessions/$id/complete", ByteArray(0)).optBoolean("confirmed")) { "Upload is not confirmed" }
    file.put("confirmed", true)
  }
}
