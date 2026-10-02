package expo.modules.digitalbrainruntime

import android.content.Context
import android.os.SystemClock
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/** Native durable uploader for samples captured by DigitalBrainRuntimeService. */
class RuntimeLocationUploadWorker(
  context: Context,
  params: WorkerParameters,
) : CoroutineWorker(context, params) {
  override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
    val startedAt = SystemClock.elapsedRealtime()
    val energyBefore = RuntimeEnergyDiagnostics.sample(applicationContext)
    val workerId = id.toString()
    RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_started", mapOf(
      "worker_id" to workerId,
      "run_attempt" to runAttemptCount,
      "owner_enabled" to (RuntimeFeature.LOCATION in DigitalBrainRuntime.owners(applicationContext)),
    ))
    var uploaded = 0
    var lastStatus: Int? = null
    var outcome = "unknown"
    var result = Result.success()
    var scheduleBacklog = false
    var retryRequested = false

    fun finish(reason: String, workResult: Result): Result {
      outcome = reason
      result = workResult
      retryRequested = workResult is Result.Retry
      return workResult
    }

    try {
      if (RuntimeFeature.LOCATION !in DigitalBrainRuntime.owners(applicationContext)) {
        RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_blocked", mapOf(
          "worker_id" to workerId,
          "reason" to "tracking_disabled",
        ))
        finish("tracking_disabled", Result.success())
      } else {
      val config = RuntimeLocationUploadConfigStore.load(applicationContext)
      if (config == null) {
          RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_blocked", mapOf(
            "worker_id" to workerId,
            "reason" to "configuration_unavailable",
          ))
          finish("configuration_unavailable", Result.success())
      } else {
          RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_config_loaded", mapOf(
            "worker_id" to workerId,
            "config_available" to true,
            "api_endpoint" to RuntimeLocationUploadConfigStore.diagnosticSnapshot(applicationContext)["endpoint"],
          ))
          val batch = RuntimeLocationStore.pendingSamples(applicationContext)
            .sortedBy(RuntimeLocationSample::timestamp)
            .take(MAX_SAMPLES_PER_RUN)
          RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_queue_loaded", mapOf(
            "worker_id" to workerId,
            "sample_count" to batch.size,
            "queue_size" to RuntimeLocationStore.pendingSamples(applicationContext).size,
          ))
          if (batch.isEmpty()) {
            finish("queue_empty", Result.success())
          } else {
            RuntimeLocationDiagnostics.record(applicationContext, "auth_token_requested", mapOf(
              "worker_id" to workerId,
              "phase" to "initial",
            ))
            val initialToken = try {
              freshIdToken(config.googleWebClientId)
            } catch (error: Exception) {
              RuntimeLocationDiagnostics.record(applicationContext, "auth_token_failed", mapOf(
                "worker_id" to workerId,
                "phase" to "initial",
                "error_type" to error.javaClass.simpleName,
              ))
              throw error
            }
            RuntimeLocationDiagnostics.record(applicationContext, "auth_token_result", mapOf(
              "worker_id" to workerId,
              "phase" to "initial",
              "auth_available" to (initialToken != null),
            ))
            if (initialToken == null) {
              RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_blocked", mapOf(
                "worker_id" to workerId,
                "reason" to "auth_unavailable",
                "queue_size" to batch.size,
              ))
              finish("auth_unavailable", Result.success())
            } else {
              var token: String = requireNotNull(initialToken)
              val deadline = startedAt + MAX_RUN_DURATION_MS
              for (sample in batch) {
                if (SystemClock.elapsedRealtime() >= deadline) {
                  scheduleBacklog = true
                  RuntimeLocationDiagnostics.record(applicationContext, "upload_run_budget_reached", mapOf(
                    "worker_id" to workerId,
                    "sample_index" to (uploaded + 1),
                    "sample_total" to batch.size,
                  ))
                  finish("run_budget_reached", Result.success())
                  break
                }
                if (RuntimeFeature.LOCATION !in DigitalBrainRuntime.owners(applicationContext)) {
                  RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_blocked", mapOf(
                    "worker_id" to workerId,
                    "reason" to "tracking_disabled_mid_run",
                    "sample_index" to (uploaded + 1),
                  ))
                  finish("tracking_disabled", Result.success())
                  break
                }

                val requestId = RuntimeLocationDebugId.requestId(sample.id)
                RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_started", mapOf(
                  "worker_id" to workerId,
                  "sample_key" to requestId,
                  "request_id" to requestId,
                  "sample_index" to (uploaded + 1),
                  "sample_total" to batch.size,
                  "captured_at" to capturedAt(sample.timestamp),
                  "capture_age_ms" to (System.currentTimeMillis() - sample.timestamp).coerceAtLeast(0),
                  "run_attempt" to runAttemptCount,
                ))
                val requestStartedAt = SystemClock.elapsedRealtime()
                var response = postSample(config.apiBaseUrl, sample, token)
                lastStatus = response.status
                RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_response", mapOf(
                  "worker_id" to workerId,
                  "sample_key" to requestId,
                  "request_id" to requestId,
                  "http_status" to response.status,
                  "http_success" to response.success,
                  "content_type" to response.contentType,
                  "response_bytes" to response.responseBytes,
                  "validation_issues" to response.validationIssues,
                  "request_duration_ms" to (SystemClock.elapsedRealtime() - requestStartedAt),
                  "error_type" to response.errorType,
                ))
                if (response.status == 401) {
                  RuntimeLocationDiagnostics.record(applicationContext, "auth_token_requested", mapOf(
                    "worker_id" to workerId,
                    "phase" to "after_401",
                    "sample_key" to requestId,
                  ))
                  val refreshedToken = try {
                    freshIdToken(config.googleWebClientId)
                  } catch (error: Exception) {
                    RuntimeLocationDiagnostics.record(applicationContext, "auth_token_failed", mapOf(
                      "worker_id" to workerId,
                      "phase" to "after_401",
                      "sample_key" to requestId,
                      "error_type" to error.javaClass.simpleName,
                    ))
                    throw error
                  }
                  RuntimeLocationDiagnostics.record(applicationContext, "auth_token_result", mapOf(
                    "worker_id" to workerId,
                    "phase" to "after_401",
                    "sample_key" to requestId,
                    "auth_available" to (refreshedToken != null),
                  ))
                  if (refreshedToken != null) {
                    RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_retry", mapOf(
                      "worker_id" to workerId,
                      "sample_key" to requestId,
                      "reason" to "http_401_token_refresh",
                    ))
                    token = refreshedToken
                    val retryStartedAt = SystemClock.elapsedRealtime()
                    response = postSample(config.apiBaseUrl, sample, token)
                    lastStatus = response.status
                    RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_response", mapOf(
                      "worker_id" to workerId,
                      "sample_key" to requestId,
                      "request_id" to requestId,
                      "phase" to "after_401_refresh",
                      "http_status" to response.status,
                      "http_success" to response.success,
                      "content_type" to response.contentType,
                      "response_bytes" to response.responseBytes,
                      "validation_issues" to response.validationIssues,
                      "request_duration_ms" to (SystemClock.elapsedRealtime() - retryStartedAt),
                      "error_type" to response.errorType,
                    ))
                  }
                }

                if (response.success) {
                  try {
                    RuntimeLocationStore.acknowledge(applicationContext, setOf(sample.id))
                  } catch (error: Exception) {
                    RuntimeLocationDiagnostics.record(applicationContext, "sample_acknowledgement_failed", mapOf(
                      "worker_id" to workerId,
                      "sample_key" to requestId,
                      "error_type" to error.javaClass.simpleName,
                    ))
                    throw error
                  }
                  uploaded++
                  outcome = "uploaded"
                  continue
                }

                when {
                  response.status == 401 -> {
                    RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_failed", mapOf(
                      "worker_id" to workerId,
                      "sample_key" to requestId,
                      "reason" to "auth_rejected",
                      "http_status" to 401,
                    ))
                    finish("auth_rejected", Result.success())
                  }
                  response.status == 408 || response.status == 429 || (response.status != null && response.status >= 500) ->
                    {
                      RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_failed", mapOf(
                        "worker_id" to workerId,
                        "sample_key" to requestId,
                        "reason" to "server_or_rate_limit",
                        "http_status" to response.status,
                      ))
                      finish("server_or_rate_limit", Result.retry())
                    }
                  response.status != null && response.status in 200..299 ->
                    {
                      RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_failed", mapOf(
                        "worker_id" to workerId,
                        "sample_key" to requestId,
                        "reason" to "unexpected_success_response",
                        "http_status" to response.status,
                      ))
                      finish("unexpected_success_response", Result.retry())
                    }
                  response.status == null -> {
                    RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_failed", mapOf(
                      "worker_id" to workerId,
                      "sample_key" to requestId,
                      "reason" to "network_error",
                      "error_type" to response.errorType,
                    ))
                    finish("network_error", Result.retry())
                  }
                  else -> {
                    RuntimeLocationDiagnostics.record(applicationContext, "sample_upload_failed", mapOf(
                      "worker_id" to workerId,
                      "sample_key" to requestId,
                      "reason" to "http_error",
                      "http_status" to response.status,
                    ))
                    finish("http_error", Result.success())
                  }
                }
                break
              }
            }
          }
        }
      }

      val remaining = RuntimeLocationStore.pendingSamples(applicationContext).size
      if (uploaded == MAX_SAMPLES_PER_RUN) scheduleBacklog = true
      if (!retryRequested && remaining > 0 && scheduleBacklog) {
        RuntimeLocationUploadScheduler.enqueue(applicationContext, "native_upload_backlog")
      }
    } catch (error: Exception) {
      RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_exception", mapOf(
        "worker_id" to workerId,
        "error_type" to error.javaClass.simpleName,
        "run_attempt" to runAttemptCount,
      ))
      Log.w("DigitalBrainRuntime", "native_upload_failed type=${error.javaClass.simpleName}")
      finish("worker_error", Result.retry())
    }

    val durationMs = SystemClock.elapsedRealtime() - startedAt
    val remaining = runCatching { RuntimeLocationStore.pendingSamples(applicationContext).size }.getOrDefault(-1)
    val energyAfter = RuntimeEnergyDiagnostics.sample(applicationContext)
    RuntimeLocationUploadStats.record(
      applicationContext,
      durationMs,
      outcome,
      uploaded,
      lastStatus,
      remaining,
      counterDelta(energyBefore, energyAfter, "processCpuMs"),
      counterDelta(energyBefore, energyAfter, "intervalDeviceAwakeMs"),
      counterDelta(energyBefore, energyAfter, "intervalBatteryChargeDeltaMicroAh"),
    )
    RuntimeLocationDiagnostics.record(applicationContext, "upload_worker_finished", mapOf(
      "worker_id" to workerId,
      "run_attempt" to runAttemptCount,
      "uploaded_count" to uploaded,
      "queue_size" to remaining,
      "outcome" to outcome,
      "result" to when (result) {
        is Result.Retry -> "retry"
        is Result.Success -> "success"
        is Result.Failure -> "failure"
        else -> "unknown"
      },
      "http_status" to lastStatus,
      "duration_ms" to durationMs,
      "retry_backoff_ms" to if (retryRequested) 300_000 else null,
    ))
    Log.i(
      "DigitalBrainRuntime",
      "native_upload_finished duration_ms=$durationMs uploaded=$uploaded queued=$remaining outcome=$outcome " +
        "process_cpu_ms=${counterDelta(energyBefore, energyAfter, "processCpuMs")} " +
        "device_awake_ms=${counterDelta(energyBefore, energyAfter, "intervalDeviceAwakeMs")} " +
        "battery_charge_delta_uah=${counterDelta(energyBefore, energyAfter, "intervalBatteryChargeDeltaMicroAh")} " +
        "http_status=${lastStatus ?: 0}",
    )
    result
  }

  private fun freshIdToken(webClientId: String): String? {
    return RuntimeGoogleIdTokenProvider.getFreshIdToken(applicationContext, webClientId)
  }

  private fun postSample(apiBaseUrl: String, sample: RuntimeLocationSample, token: String): UploadResponse {
    val connection = (URL("${apiBaseUrl.trimEnd('/')}/mobile/location").openConnection() as HttpURLConnection)
    val requestId = RuntimeLocationDebugId.requestId(sample.id)
    var phase = "connect"
    val startedAt = SystemClock.elapsedRealtime()
    return try {
      connection.requestMethod = "POST"
      connection.connectTimeout = REQUEST_TIMEOUT_MS
      connection.readTimeout = REQUEST_TIMEOUT_MS
      connection.instanceFollowRedirects = false
      connection.doOutput = true
      connection.setRequestProperty("Authorization", "Bearer $token")
      connection.setRequestProperty("Content-Type", "application/json")
      connection.setRequestProperty("Accept", "application/json")
      connection.setRequestProperty("x-location-debug-request-id", requestId)
      connection.setRequestProperty("x-location-debug-batch-id", requestId)
      connection.setRequestProperty("x-location-debug-sample-index", "1")
      connection.setRequestProperty("x-location-debug-sample-count", "1")
      connection.setRequestProperty("x-location-debug-captured-at", capturedAt(sample.timestamp))
      connection.setRequestProperty("x-location-debug-app-state", "background")

      val payload = JSONObject()
        .put("lat", sample.latitude)
        .put("lon", sample.longitude)
        .put("captured_at", capturedAt(sample.timestamp))
        .put("source", "android_foreground_location")
        .put("timezone", sample.timezone)
      if (sample.accuracy != null) payload.put("accuracy_m", sample.accuracy)
      phase = "write_request"
      connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }

      phase = "read_response_status"
      val status = connection.responseCode
      val contentType = connection.contentType.orEmpty()
      val validSuccess = status in 200..299 && (status == 204 || contentType.contains("application/json", ignoreCase = true))
      val responseKind = when {
        contentType.contains("application/json", ignoreCase = true) -> "json"
        contentType.isBlank() -> "missing"
        else -> "non_json"
      }
      val validationBody = if (status == 422) readBounded(connection.errorStream, MAX_DIAGNOSTIC_RESPONSE_BYTES) else null
      val validationIssues = validationBody?.let(::safeValidationIssues).orEmpty()
      UploadResponse(status, validSuccess, responseKind, null, validationBody?.size, validationIssues).also { response ->
        RuntimeLocationDiagnostics.record(applicationContext, "http_request_completed", mapOf(
          "sample_key" to requestId,
          "request_id" to requestId,
          "http_status" to status,
          "http_success" to validSuccess,
          "content_type" to responseKind,
          "response_bytes" to response.responseBytes,
          "validation_issues" to response.validationIssues,
          "request_duration_ms" to (SystemClock.elapsedRealtime() - startedAt),
        ))
      }
    } catch (error: IOException) {
      RuntimeLocationDiagnostics.record(applicationContext, "http_request_failed", mapOf(
        "sample_key" to requestId,
        "request_id" to requestId,
        "phase" to phase,
        "error_type" to error.javaClass.simpleName,
        "request_duration_ms" to (SystemClock.elapsedRealtime() - startedAt),
      ))
      UploadResponse(null, false, "unknown", error.javaClass.simpleName, null, emptyList())
    } finally {
      connection.disconnect()
    }
  }

  private fun capturedAt(timestamp: Long): String = SimpleDateFormat(
    "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
    Locale.US,
  ).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date(timestamp))

  private fun readBounded(stream: InputStream?, limit: Int): ByteArray? {
    if (stream == null) return null
    return stream.use { input ->
      val output = ByteArrayOutputStream()
      val buffer = ByteArray(1024)
      var remaining = limit
      while (remaining > 0) {
        val count = input.read(buffer, 0, minOf(buffer.size, remaining))
        if (count <= 0) break
        output.write(buffer, 0, count)
        remaining -= count
      }
      output.toByteArray()
    }
  }

  /** Keep FastAPI validation types and field paths; never copy input values or message text. */
  private fun safeValidationIssues(body: ByteArray): List<String> = runCatching {
    val details = JSONObject(String(body, Charsets.UTF_8)).optJSONArray("detail")
      ?: return@runCatching emptyList()
    (0 until minOf(details.length(), MAX_VALIDATION_ISSUES)).mapNotNull { index ->
      val item = details.optJSONObject(index) ?: return@mapNotNull null
      val type = item.optString("type")
        .takeIf { it.matches(Regex("[a-zA-Z0-9_.-]{1,64}")) } ?: "unknown"
      val location = item.optJSONArray("loc")
      val safePath = location?.let { loc ->
        (0 until loc.length()).mapNotNull { pathIndex ->
          when (val value = loc.opt(pathIndex)) {
            is String -> value.takeIf { it.matches(Regex("[a-zA-Z0-9_.-]{1,64}")) }
            is Number -> "#"
            else -> null
          }
        }.joinToString(".")
      }.orEmpty()
      if (safePath.isBlank()) type else "$type:$safePath"
    }
  }.getOrDefault(emptyList())

  private fun counterDelta(before: Map<String, Any?>, after: Map<String, Any?>, key: String): Long {
    val start = before[key] as? Number ?: return 0L
    val end = after[key] as? Number ?: return 0L
    return end.toLong() - start.toLong()
  }

  private data class UploadResponse(
    val status: Int?,
    val success: Boolean,
    val contentType: String,
    val errorType: String?,
    val responseBytes: Int?,
    val validationIssues: List<String>,
  )

  companion object {
    private const val MAX_SAMPLES_PER_RUN = 50
    private const val MAX_RUN_DURATION_MS = 45_000L
    private const val REQUEST_TIMEOUT_MS = 15_000
    private const val MAX_DIAGNOSTIC_RESPONSE_BYTES = 8_192
    private const val MAX_VALIDATION_ISSUES = 8
  }
}
