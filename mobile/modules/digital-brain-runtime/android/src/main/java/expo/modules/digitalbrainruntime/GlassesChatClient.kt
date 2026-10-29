package expo.modules.digitalbrainruntime

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Native authenticated chat. No retries, audio files, JS, or recovery dispatch. */
internal class GlassesChatClient(private val c: Context, private val guard: () -> Unit) {
  @Volatile private var connection: HttpURLConnection? = null
  @Volatile private var cancelled = false
  fun cancel() { cancelled = true; connection?.disconnect() }
  private fun current() { check(!cancelled); guard() }
  fun request(transcript: String, requestId: String, capturedAt: Long, timezone: String): JSONObject {
    current()
    check(System.currentTimeMillis() - capturedAt in -10_000L..90_000L)
    val config = checkNotNull(RuntimeLocationUploadConfigStore.load(c))
    val token = checkNotNull(RuntimeGoogleIdTokenProvider.getSpeechIdToken(c, config.googleWebClientId))
    current()
    val http = URL(config.apiBaseUrl.trimEnd('/') + "/mobile/glasses/chat").openConnection() as HttpURLConnection
    connection = http
    try {
      current()
      http.requestMethod = "POST"; http.instanceFollowRedirects = false
      http.connectTimeout = 5_000; http.readTimeout = 65_000
      http.setRequestProperty("Authorization", "Bearer ${token.value}")
      http.setRequestProperty("Content-Type", "application/json")
      val body = JSONObject().put("transcript", transcript).put("request_id", requestId)
        .put("captured_at_ms", capturedAt).put("timezone", timezone).toString().toByteArray(Charsets.UTF_8)
      http.doOutput = true; http.setFixedLengthStreamingMode(body.size)
      http.outputStream.use { it.write(body) }
      current()
      val status = http.responseCode
      if (status == 401) RuntimeGoogleIdTokenProvider.rejectSpeechToken(token.value)
      check(status == 200)
      val output = java.io.ByteArrayOutputStream()
      http.inputStream.use { input ->
        val buffer = ByteArray(8192)
        while (true) {
          current()
          val count = input.read(buffer)
          if (count < 0) break
          check(output.size() + count <= 6 * 1024 * 1024)
          output.write(buffer, 0, count)
        }
      }
      current()
      return JSONObject(output.toString("UTF-8"))
    } finally { http.disconnect(); connection = null }
  }
}
