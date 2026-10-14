package expo.modules.digitalbrainruntime

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal enum class GlassesCommandOutcome(val key: String) {
  ACCEPTED("accepted"), FAILED("failed"), UNKNOWN("unknown"), EXPIRED("expired"),
  NOT_CONFIGURED("not_configured"), CANCELLED("cancelled"), REJECTED_WAKE("rejected_wake"),
  UNRECOGNIZED("unrecognized")
}

/** Runs on the existing bounded speech worker. No retries or delayed/offline action queue. */
internal object GlassesCommandExecutor {
  fun execute(c: Context, decision: GlassesCommandDecision, requestId: String, capturedAtMs: Long,
              eligible: () -> Boolean): GlassesCommandOutcome {
    if (!decision.wakeVerified) return GlassesCommandOutcome.REJECTED_WAKE
    val command = decision.command ?: return GlassesCommandOutcome.UNRECOGNIZED
    fun current() = runBlocking { withContext(Dispatchers.Main) { eligible() } }
    if (!current()) return GlassesCommandOutcome.CANCELLED
    if (System.currentTimeMillis() - capturedAtMs !in -10_000L..90_000L) return GlassesCommandOutcome.EXPIRED
    if (command == GlassesCommand.TAKE_PICTURE) return runBlocking {
      withContext(Dispatchers.Main) {
        if (!eligible()) GlassesCommandOutcome.CANCELLED else RuntimeGlasses.takeWakePhoto(requestId)
      }
    }
    val config = RuntimeLocationUploadConfigStore.load(c) ?: return GlassesCommandOutcome.NOT_CONFIGURED
    val token = runCatching { RuntimeGoogleIdTokenProvider.getFreshIdToken(c, config.googleWebClientId) }
      .getOrNull() ?: return GlassesCommandOutcome.FAILED
    if (!current()) return GlassesCommandOutcome.CANCELLED
    val connection = URL(config.apiBaseUrl.trimEnd('/') + "/mobile/glasses/commands").openConnection() as HttpURLConnection
    return try {
      connection.requestMethod = "POST"; connection.connectTimeout = 5000; connection.readTimeout = 15000
      connection.instanceFollowRedirects = false
      connection.setRequestProperty("Authorization", "Bearer $token")
      connection.setRequestProperty("Content-Type", "application/json")
      val body = JSONObject().put("command", command.key).put("request_id", requestId)
        .put("captured_at_ms", capturedAtMs).toString().toByteArray()
      connection.doOutput = true; connection.setFixedLengthStreamingMode(body.size)
      if (!current()) return GlassesCommandOutcome.CANCELLED
      connection.outputStream.use { it.write(body) }
      if (connection.responseCode !in 200..299) GlassesCommandOutcome.UNKNOWN else {
        val status = connection.inputStream.use {
          JSONObject(String(GlassesMediaStore.readBounded(it, 4096))).optString("status")
        }
        GlassesCommandOutcome.entries.firstOrNull { it.key == status } ?: GlassesCommandOutcome.UNKNOWN
      }
    } catch (_: Exception) { GlassesCommandOutcome.UNKNOWN }
    finally { connection.disconnect() }
  }
}
