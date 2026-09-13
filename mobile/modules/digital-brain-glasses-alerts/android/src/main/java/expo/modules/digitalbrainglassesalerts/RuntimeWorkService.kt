package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.content.Intent
import android.util.Log
import android.os.SystemClock
import com.facebook.react.HeadlessJsTaskService
import com.facebook.react.bridge.Arguments
import com.facebook.react.jstasks.HeadlessJsTaskConfig

/** A bounded JS wake-up under the already-running foreground service, with no second notification. */
class RuntimeWorkService : HeadlessJsTaskService() {
  companion object {
    private var running = false
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
  override fun getTaskConfig(intent: Intent?) = HeadlessJsTaskConfig(
    "DigitalBrainRuntimeWork", Arguments.createMap().apply { putString("reason", intent?.getStringExtra("reason") ?: "unknown") }, 120_000L, true,
  )
  override fun onDestroy() {
    lastDurationMs = SystemClock.elapsedRealtime() - startedAtMs
    Log.i("DigitalBrainRuntime", "runtime_work_finished duration_ms=$lastDurationMs")
    running = false
    super.onDestroy()
  }
}
