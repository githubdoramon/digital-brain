package expo.modules.digitalbrainruntime

import android.content.Context
import android.os.SystemClock
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Completed wake audio only. Provider credentials are held exclusively by the backend. */
internal class GlassesTranscriptionClient(private val c: Context, private val guard: () -> Unit) {
  @Volatile private var connection: HttpURLConnection? = null
  fun cancel() { connection?.disconnect() }
  fun request(audio: ByteArray = byteArrayOf(), replay: Boolean = false, prepare: Boolean = false, candidate: String = SpeechEvalServerCandidate.ID): JSONObject {
    guard()
    val config = RuntimeLocationUploadConfigStore.load(c) ?: throw SpeechEvalFailure("remote_not_configured")
    val started = SystemClock.elapsedRealtime()
    val token = RuntimeGoogleIdTokenProvider.getFreshIdToken(c, config.googleWebClientId)
      ?: throw SpeechEvalFailure("auth_rejected")
    guard()
    val authMs = SystemClock.elapsedRealtime() - started
    val selection = SpeechEvalServerCandidate.parse(if (replay) candidate else SpeechEvalServerCandidate.ID) ?: throw SpeechEvalFailure("invalid_model")
    val path = if (replay) "/mobile/glasses/speech-eval/${if (prepare) "prepare" else "transcribe"}?provider=remote&model=${selection.model}" else "/mobile/glasses/transcribe"
    val http = URL(config.apiBaseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
    connection = http
    try {
      guard()
      http.requestMethod = "POST"; http.instanceFollowRedirects = false
      http.connectTimeout = 5_000; http.readTimeout = 25_000
      http.setRequestProperty("Authorization", "Bearer $token")
      http.setRequestProperty("Content-Type", "audio/wav")
      http.doOutput = true; http.setFixedLengthStreamingMode(audio.size)
      val writeAt = SystemClock.elapsedRealtime()
      http.outputStream.use { it.write(audio) }
      val writeMs = SystemClock.elapsedRealtime() - writeAt
      guard()
      val responseAt = SystemClock.elapsedRealtime()
      val status = http.responseCode
      if (status !in 200..299) {
        val body = runCatching { JSONObject(String(read(http.errorStream), Charsets.UTF_8)) }.getOrNull()
        throw SpeechEvalFailures.http(status, body)
      }
      val result = JSONObject(String(read(http.inputStream), Charsets.UTF_8))
      selection.validateResponse(result)
      if (!prepare && (result.opt("text") !is String || result.getString("text").length > 8192))
        throw SpeechEvalFailure("remote_invalid_response")
      guard()
      return result.put("authMs", authMs).put("requestWriteMs", writeMs)
        .put("responseWaitMs", SystemClock.elapsedRealtime() - responseAt)
        .put("roundTripMs", SystemClock.elapsedRealtime() - started)
    } finally { http.disconnect(); connection = null }
  }
  private fun read(stream: java.io.InputStream?): ByteArray {
    val input = stream ?: throw SpeechEvalFailure("remote_invalid_response")
    return input.use {
      val output = java.io.ByteArrayOutputStream(); val buffer = ByteArray(4096)
      while (true) {
        guard()
        val count = it.read(buffer)
        if (count < 0) break
        if (output.size() + count > 32768) throw SpeechEvalFailure("remote_invalid_response")
        output.write(buffer, 0, count)
      }
      output.toByteArray()
    }
  }
}
