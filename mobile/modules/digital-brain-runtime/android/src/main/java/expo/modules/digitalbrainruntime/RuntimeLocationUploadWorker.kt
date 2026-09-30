package expo.modules.digitalbrainruntime

import android.content.Context
import android.os.SystemClock
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
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
        finish("tracking_disabled", Result.success())
      } else {
        val config = RuntimeLocationUploadConfigStore.load(applicationContext)
        if (config == null) {
          finish("configuration_unavailable", Result.success())
        } else {
          val batch = RuntimeLocationStore.pendingSamples(applicationContext)
            .sortedBy(RuntimeLocationSample::timestamp)
            .take(MAX_SAMPLES_PER_RUN)
          if (batch.isEmpty()) {
            finish("queue_empty", Result.success())
          } else {
            val initialToken = freshIdToken(config.googleWebClientId)
            if (initialToken == null) {
              finish("auth_unavailable", Result.success())
            } else {
              var token: String = requireNotNull(initialToken)
              val deadline = startedAt + MAX_RUN_DURATION_MS
              for (sample in batch) {
                if (SystemClock.elapsedRealtime() >= deadline) {
                  scheduleBacklog = true
                  finish("run_budget_reached", Result.success())
                  break
                }
                if (RuntimeFeature.LOCATION !in DigitalBrainRuntime.owners(applicationContext)) {
                  finish("tracking_disabled", Result.success())
                  break
                }

                var response = postSample(config.apiBaseUrl, sample, token)
                lastStatus = response.status
                if (response.status == 401) {
                  val refreshedToken = freshIdToken(config.googleWebClientId)
                  if (refreshedToken != null) {
                    token = refreshedToken
                    response = postSample(config.apiBaseUrl, sample, token)
                    lastStatus = response.status
                  }
                }

                if (response.success) {
                  RuntimeLocationStore.acknowledge(applicationContext, setOf(sample.id))
                  uploaded++
                  outcome = "uploaded"
                  continue
                }

                when {
                  response.status == 401 -> finish("auth_rejected", Result.success())
                  response.status == 408 || response.status == 429 || (response.status != null && response.status >= 500) ->
                    finish("server_or_rate_limit", Result.retry())
                  response.status != null && response.status in 200..299 ->
                    finish("unexpected_success_response", Result.retry())
                  response.status == null -> finish("network_error", Result.retry())
                  else -> finish("http_error", Result.success())
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
    )
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
    val requestId = debugId(sample.id)
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
      connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }

      val status = connection.responseCode
      val contentType = connection.contentType.orEmpty()
      val validSuccess = status in 200..299 && (status == 204 || contentType.contains("application/json", ignoreCase = true))
      if (!validSuccess && status in 200..299) UploadResponse(status, false)
      else UploadResponse(status, validSuccess)
    } catch (error: IOException) {
      UploadResponse(null, false)
    } finally {
      connection.disconnect()
    }
  }

  private fun capturedAt(timestamp: Long): String = SimpleDateFormat(
    "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
    Locale.US,
  ).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date(timestamp))

  private fun debugId(id: String): String {
    val digest = java.security.MessageDigest.getInstance("SHA-256")
      .digest(id.toByteArray(Charsets.UTF_8))
      .take(10)
      .joinToString("") { "%02x".format(it) }
    return "android-native-$digest"
  }

  private fun counterDelta(before: Map<String, Any?>, after: Map<String, Any?>, key: String): Long {
    val start = before[key] as? Number ?: return 0L
    val end = after[key] as? Number ?: return 0L
    return end.toLong() - start.toLong()
  }

  private data class UploadResponse(val status: Int?, val success: Boolean)

  companion object {
    private const val MAX_SAMPLES_PER_RUN = 50
    private const val MAX_RUN_DURATION_MS = 45_000L
    private const val REQUEST_TIMEOUT_MS = 15_000
  }
}
