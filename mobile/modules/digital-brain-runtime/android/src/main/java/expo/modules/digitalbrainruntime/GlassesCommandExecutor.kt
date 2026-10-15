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
              eligible: () -> Boolean, onEvent: (CommandDispatchEvent) -> Unit = {}): GlassesCommandOutcome {
    val started = android.os.SystemClock.elapsedRealtime()
    val debugRef = java.security.MessageDigest.getInstance("SHA-256").digest(requestId.toByteArray())
      .take(6).joinToString("") { "%02x".format(it) }
    fun event(value: CommandDispatchEvent) {
      val timed = value.copy(elapsedMs = android.os.SystemClock.elapsedRealtime() - started)
      android.util.Log.i("GlassesCommand", "ref=$debugRef stage=${timed.stage.name} http=${timed.httpStatus ?: 0} failure=${timed.failure?.name ?: "none"} elapsedMs=${timed.elapsedMs}")
      onEvent(timed)
    }
    fun finish(outcome: GlassesCommandOutcome, failure: CommandDispatchFailure? = null): GlassesCommandOutcome {
      event(CommandDispatchEvent(CommandDispatchStage.FINISHED, failure = failure))
      return outcome
    }
    event(CommandDispatchEvent(CommandDispatchStage.VALIDATION))
    if (!decision.wakeVerified) return finish(GlassesCommandOutcome.REJECTED_WAKE)
    val command = decision.command ?: return finish(GlassesCommandOutcome.UNRECOGNIZED)
    fun current() = runBlocking { withContext(Dispatchers.Main) { eligible() } }
    if (!current()) return finish(GlassesCommandOutcome.CANCELLED, CommandDispatchFailure.CANCELLED)
    if (System.currentTimeMillis() - capturedAtMs !in -10_000L..90_000L)
      return finish(GlassesCommandOutcome.EXPIRED, CommandDispatchFailure.EXPIRED)
    if (command == GlassesCommand.TAKE_PICTURE) return finish(runBlocking {
      withContext(Dispatchers.Main) {
        if (!eligible()) GlassesCommandOutcome.CANCELLED else RuntimeGlasses.takeWakePhoto(requestId)
      }
    })
    event(CommandDispatchEvent(CommandDispatchStage.CONFIGURATION))
    val config = RuntimeLocationUploadConfigStore.load(c)
      ?: run {
        event(CommandDispatchEvent(CommandDispatchStage.CONFIGURATION,
          failure = CommandDispatchFailure.CONFIGURATION,
          configurationIssues = listOf(CommandConfigurationIssue.PHONE_API_CONFIGURATION_MISSING)))
        return finish(GlassesCommandOutcome.NOT_CONFIGURED, CommandDispatchFailure.CONFIGURATION)
      }
    val url = URL(config.apiBaseUrl.trimEnd('/') + "/mobile/glasses/commands")
    // The actual destination belongs only in the private capture, never in Logcat/telemetry.
    val endpoint = java.net.URI(url.protocol, null, url.host, url.port, url.path, null, null).toString()
    event(CommandDispatchEvent(CommandDispatchStage.CONFIGURATION, endpoint = endpoint))
    event(CommandDispatchEvent(CommandDispatchStage.AUTHENTICATION))
    val token = runCatching { RuntimeGoogleIdTokenProvider.getFreshIdToken(c, config.googleWebClientId) }
      .getOrNull() ?: return finish(GlassesCommandOutcome.FAILED, CommandDispatchFailure.AUTHENTICATION)
    if (!current()) return finish(GlassesCommandOutcome.CANCELLED, CommandDispatchFailure.CANCELLED)
    if (url.protocol == "http" && !android.security.NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted(url.host))
      return finish(GlassesCommandOutcome.UNKNOWN, CommandDispatchFailure.CLEARTEXT_BLOCKED)
    return try {
      val connection = url.openConnection() as HttpURLConnection
      connection.setRequestProperty("x-glasses-command-debug-ref", debugRef)
      val body = JSONObject().put("command", command.key).put("request_id", requestId)
        .put("captured_at_ms", capturedAtMs).toString().toByteArray()
      GlassesCommandDispatch.post(connection, body, token, ::current, ::event) { bytes ->
        val result = JSONObject(String(bytes))
        val status = result.getString("status")
        val outcome = checkNotNull(GlassesCommandOutcome.entries.firstOrNull { it.key == status })
        if (outcome == GlassesCommandOutcome.NOT_CONFIGURED) {
          val raw = result.optJSONArray("configuration_issues")
          val issues = (0 until minOf(raw?.length() ?: 0, 4)).mapNotNull { i ->
            CommandConfigurationIssue.entries.firstOrNull {
              it != CommandConfigurationIssue.PHONE_API_CONFIGURATION_MISSING && it.key == raw?.optString(i)
            }
          }.distinct()
          event(CommandDispatchEvent(CommandDispatchStage.CONFIGURATION,
            failure = CommandDispatchFailure.BACKEND_CONFIGURATION, configurationIssues = issues))
        }
        outcome
      }
    } catch (error: Exception) {
      finish(GlassesCommandOutcome.UNKNOWN, GlassesCommandDispatch.failure(error))
    }
  }
}
