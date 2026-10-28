package expo.modules.digitalbrainruntime

import org.json.JSONObject

/** Failure metadata is allow-listed. Never export platform exception text or response bodies. */
internal class SpeechEvalFailure(
  val reason: String,
  val stage: String? = null,
  val httpStatus: Int? = null,
  val upstreamStatus: Int? = null,
) : Exception(reason)

internal object SpeechEvalFailures {
  val reasons = setOf("preparation_failed", "insufficient_memory", "warm_context_lost", "inference_timeout",
    "audio_changed", "server_busy", "server_timeout", "evaluation_disabled", "inference_failed",
    "engine_unavailable", "invalid_audio", "invalid_model", "engine_import_failed", "model_download_http_error",
    "engine_http_error", "cache_storage_full", "cache_access_denied", "network_timeout",
    "model_download_unavailable", "model_download_failed", "model_load_failed", "network_unavailable",
    "tls_failed", "server_update_required", "cpu_configuration_mismatch", "invalid_cpu_threads", "auth_rejected", "endpoint_unavailable", "upstream_unavailable", "remote_not_configured", "remote_http_error", "remote_invalid_response", "evaluation_failed")
  private val stages = setOf("request", "initialization", "model_download", "model_load", "inference")
  fun validateCpuResponse(response: JSONObject, requestedThreads: Int?) {
    if (requestedThreads == null) return
    if (!response.has("cpuThreads") || response.isNull("cpuThreads")) {
      throw SpeechEvalFailure("server_update_required", "request", 200)
    }
    if (response.optInt("cpuThreads", -1) != requestedThreads) {
      throw SpeechEvalFailure("cpu_configuration_mismatch", "request", 200)
    }
  }
  fun counts(rows: List<JSONObject>): Map<String, Int> = rows
    .filter { it.optString("status") == "failed" }
    .map { it.optString("error").takeIf { reason -> reason in reasons } ?: "evaluation_failed" }
    .groupingBy { it }.eachCount()
  fun http(status: Int, body: JSONObject?): SpeechEvalFailure {
    val fallback = when (status) {
      401, 403 -> "auth_rejected"
      404 -> "endpoint_unavailable"
      408, 504 -> "server_timeout"
      429 -> "server_busy"
      in 500..599 -> "upstream_unavailable"
      else -> "evaluation_failed"
    }
    val reason = body?.optString("detail")?.takeIf { it in reasons } ?: fallback
    val stage = body?.optString("failureStage")?.takeIf { it in stages }
    val upstream = body?.optInt("upstreamHttpStatus", 0)?.takeIf { it in 100..599 }
    return SpeechEvalFailure(reason, stage, status, upstream)
  }
  fun annotate(row: JSONObject, error: Exception) {
    val reason = when (error) {
      is SpeechEvalFailure -> error.reason.takeIf { it in reasons } ?: "evaluation_failed"
      is java.net.SocketTimeoutException -> "network_timeout"
      is java.net.UnknownHostException, is java.net.ConnectException -> "network_unavailable"
      is javax.net.ssl.SSLException -> "tls_failed"
      else -> error.message?.takeIf { it in reasons } ?: "evaluation_failed"
    }
    row.put("error", reason)
    if (error is SpeechEvalFailure) {
      error.stage?.takeIf { it in stages }?.let { row.put("failureStage", it) }
      error.httpStatus?.let { row.put("httpStatus", it) }
      error.upstreamStatus?.let { row.put("upstreamHttpStatus", it) }
    }
  }
}
