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
    val workerId = id.toString()

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

            val initialToken = try {
              freshIdToken(config.googleWebClientId)
            } catch (error: Exception) {

              throw error
            }

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

                val requestId = RuntimeLocationDebugId.requestId(sample.id)

                val requestStartedAt = SystemClock.elapsedRealtime()
                var response = postSample(config.apiBaseUrl, sample, token)
                lastStatus = response.status

                if (response.status == 401) {

                  val refreshedToken = try {
                    freshIdToken(config.googleWebClientId)
                  } catch (error: Exception) {

                    throw error
                  }

                  if (refreshedToken != null) {

                    token = refreshedToken
                    val retryStartedAt = SystemClock.elapsedRealtime()
                    response = postSample(config.apiBaseUrl, sample, token)
                    lastStatus = response.status

                  }
                }

                if (response.success) {
                  try {
                    RuntimeLocationStore.acknowledge(applicationContext, setOf(sample.id))
                  } catch (error: Exception) {

                    throw error
                  }
                  uploaded++
                  outcome = "uploaded"
                  continue
                }

                when {
                  response.status == 401 -> {

                    finish("auth_rejected", Result.success())
                  }
                  response.status == 408 || response.status == 429 || (response.status != null && response.status >= 500) ->
                    {

                      finish("server_or_rate_limit", Result.retry())
                    }
                  response.status != null && response.status in 200..299 ->
                    {

                      finish("unexpected_success_response", Result.retry())
                    }
                  response.status == null -> {

                    finish("network_error", Result.retry())
                  }
                  else -> {

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

      Log.w("DigitalBrainRuntime", "native_upload_failed type=${error.javaClass.simpleName}")
      finish("worker_error", Result.retry())
    }

    val durationMs = SystemClock.elapsedRealtime() - startedAt
    val remaining = runCatching { RuntimeLocationStore.pendingSamples(applicationContext).size }.getOrDefault(-1)
    RuntimeLocationUploadStats.record(
      applicationContext,
      durationMs,
      outcome,
      uploaded,
      lastStatus,
      remaining,
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
      UploadResponse(status, validSuccess, responseKind, null, validationBody?.size, validationIssues)
    } catch (error: IOException) {

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
