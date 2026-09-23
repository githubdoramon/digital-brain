package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.content.Intent
import android.util.Log
import android.os.SystemClock
import android.os.Handler
import android.os.Looper
import java.util.UUID
import com.facebook.react.HeadlessJsTaskService
import com.facebook.react.bridge.Arguments
import com.facebook.react.jstasks.HeadlessJsTaskConfig

private enum class WorkFinishReason(val key: String) {
  ACKNOWLEDGED("js_acknowledged"),
  NEAR_TIMEOUT("teardown_near_timeout"), DESTROYED("rn_or_external_teardown")
}

/** A bounded JS wake-up under the already-running foreground service, with no second notification. */
class RuntimeWorkService : HeadlessJsTaskService() {
  companion object {
    private var running = false
    private var instance: RuntimeWorkService? = null
    var totalDurationMs = 0L
      private set
    var lastFinishReason = "none"
      private set
    fun complete(token: String) {
      Handler(Looper.getMainLooper()).post {
        val worker = instance ?: return@post
        if (token != worker.token) return@post
        worker.finishReason = WorkFinishReason.ACKNOWLEDGED
        // Stop only this token's service. RN owns its task registry and may
        // notify completion normally or expire its metadata at the bounded timeout.
        worker.stopSelf()
      }
    }
    var requestCount = 0L
      private set
    var lastDurationMs = 0L
      private set
    fun request(context: Context, reason: String) {
      if (running) {
        Log.i("DigitalBrainRuntime", "runtime_work_coalesced reason=$reason")
        return
      }
      running = true
      requestCount++
      Log.i("DigitalBrainRuntime", "runtime_work_requested reason=$reason count=$requestCount")
      try {
        context.startService(Intent(context, RuntimeWorkService::class.java).putExtra("reason", reason))
      } catch (error: RuntimeException) {
        running = false
        Log.w("DigitalBrainRuntime", "runtime_work_start_rejected", error)
      }
    }
  }
  private val startedAtMs = SystemClock.elapsedRealtime()
  private val token = UUID.randomUUID().toString()
  private var finishReason = WorkFinishReason.DESTROYED
  override fun onCreate() { super.onCreate(); instance = this; running = true }
  override fun getTaskConfig(intent: Intent?) = HeadlessJsTaskConfig(
    "DigitalBrainRuntimeWork", Arguments.createMap().apply {
      putString("reason", intent?.getStringExtra("reason") ?: "unknown")
      putString("workToken", token)
    }, 120_000L, true,
  )
  override fun onDestroy() {
    lastDurationMs = SystemClock.elapsedRealtime() - startedAtMs
    totalDurationMs += lastDurationMs
    if (finishReason != WorkFinishReason.ACKNOWLEDGED && lastDurationMs >= 120_000L) finishReason = WorkFinishReason.NEAR_TIMEOUT
    lastFinishReason = finishReason.key
    Log.i("DigitalBrainRuntime", "runtime_work_finished duration_ms=$lastDurationMs reason=$lastFinishReason")
    if (instance === this) instance = null
    running = false
    super.onDestroy()
  }
}
