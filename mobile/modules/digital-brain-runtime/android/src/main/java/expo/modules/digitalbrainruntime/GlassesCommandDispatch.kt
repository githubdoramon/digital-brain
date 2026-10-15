package expo.modules.digitalbrainruntime

import java.net.HttpURLConnection

internal enum class CommandDispatchStage { VALIDATION, CONFIGURATION, AUTHENTICATION, CONNECTING, SENDING, RESPONSE, FINISHED }
internal enum class CommandDispatchFailure {
  CONFIGURATION, BACKEND_CONFIGURATION, AUTHENTICATION, CANCELLED, EXPIRED, DNS, CONNECTION, TIMEOUT, TLS,
  CLEARTEXT_BLOCKED, NETWORK, HTTP_ERROR, REDIRECT, INVALID_RESPONSE, OTHER
}
internal enum class CommandConfigurationIssue(val key: String) {
  PHONE_API_CONFIGURATION_MISSING("phone_api_configuration_missing"),
  HA_URL_MISSING("ha_url_missing"), HA_TOKEN_MISSING("ha_token_missing"),
  SCRIPT_ENTITY_MISSING("script_entity_missing"), SCRIPT_ENTITY_INVALID("script_entity_invalid")
}
internal data class CommandDispatchEvent(val stage: CommandDispatchStage, val httpStatus: Int? = null,
  val failure: CommandDispatchFailure? = null, val elapsedMs: Long = 0, val endpoint: String? = null, val configurationIssues: List<CommandConfigurationIssue> = emptyList())

internal object GlassesCommandDispatch {
  fun failure(error: Exception): CommandDispatchFailure {
    var cause: Throwable? = error
    repeat(6) {
      when (cause) {
        is java.net.UnknownHostException -> return CommandDispatchFailure.DNS
        is java.net.SocketTimeoutException -> return CommandDispatchFailure.TIMEOUT
        is javax.net.ssl.SSLException -> return CommandDispatchFailure.TLS
        is java.net.ConnectException, is java.net.NoRouteToHostException -> return CommandDispatchFailure.CONNECTION
      }
      cause = cause?.cause
    }
    return if (error is java.io.IOException) CommandDispatchFailure.NETWORK else CommandDispatchFailure.OTHER
  }

  /** No network retry. Preserve HTTP and safe transport evidence without exporting bodies/errors. */
  fun post(connection: HttpURLConnection, body: ByteArray, token: String, eligible: () -> Boolean,
           event: (CommandDispatchEvent) -> Unit, parse: (ByteArray) -> GlassesCommandOutcome): GlassesCommandOutcome {
    var responseStatus: Int? = null
    return try {
      connection.requestMethod = "POST"; connection.connectTimeout = 5000; connection.readTimeout = 15000
      connection.instanceFollowRedirects = false
      connection.setRequestProperty("Authorization", "Bearer $token")
      connection.setRequestProperty("Content-Type", "application/json")
      connection.doOutput = true; connection.setFixedLengthStreamingMode(body.size)
      if (!eligible()) {
        event(CommandDispatchEvent(CommandDispatchStage.FINISHED, failure = CommandDispatchFailure.CANCELLED))
        return GlassesCommandOutcome.CANCELLED
      }
      event(CommandDispatchEvent(CommandDispatchStage.CONNECTING))
      val output = connection.outputStream
      event(CommandDispatchEvent(CommandDispatchStage.SENDING))
      output.use { it.write(body) }
      responseStatus = connection.responseCode
      event(CommandDispatchEvent(CommandDispatchStage.RESPONSE, responseStatus))
      if (responseStatus !in 200..299) {
        event(CommandDispatchEvent(CommandDispatchStage.FINISHED, responseStatus,
          if (responseStatus in 300..399) CommandDispatchFailure.REDIRECT else CommandDispatchFailure.HTTP_ERROR))
        GlassesCommandOutcome.UNKNOWN
      } else {
        val result = try {
          connection.inputStream.use { parse(it.readBytesBounded(4096)) }
        } catch (_: Exception) {
          event(CommandDispatchEvent(CommandDispatchStage.FINISHED, responseStatus, CommandDispatchFailure.INVALID_RESPONSE))
          return GlassesCommandOutcome.UNKNOWN
        }
        event(CommandDispatchEvent(CommandDispatchStage.FINISHED, responseStatus))
        result
      }
    } catch (error: Exception) {
      event(CommandDispatchEvent(CommandDispatchStage.FINISHED, responseStatus, failure(error)))
      GlassesCommandOutcome.UNKNOWN
    } finally { connection.disconnect() }
  }

  private fun java.io.InputStream.readBytesBounded(limit: Int): ByteArray {
    val output = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(1024)
    while (true) {
      val count = read(buffer)
      if (count < 0) return output.toByteArray()
      check(output.size() + count <= limit)
      output.write(buffer, 0, count)
    }
  }
}
