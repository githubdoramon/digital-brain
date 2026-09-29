package expo.modules.digitalbrainruntime

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat

/** Native owner for background location capture. */
object DigitalBrainRuntime {
  private const val PREFS = "digital_brain_runtime"
  private val features = RuntimeFeatures()
  private var loaded = false
  private val handler = Handler(Looper.getMainLooper())
  @Volatile var service: DigitalBrainRuntimeService? = null
  @Volatile var activityVisible = false
  @Volatile var lastError: String? = null

  @Synchronized fun owners(context: Context): Set<RuntimeFeature> {
    if (!loaded) {
      val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      features.restore(prefs.getStringSet("owners", emptySet()) ?: emptySet())
      loaded = true
    }
    return features.snapshot()
  }

  @JvmStatic @Synchronized fun setFeature(context: Context, key: String, enabled: Boolean) {
    val feature = requireNotNull(RuntimeFeature.fromKey(key)) { "Unknown runtime feature: $key" }
    owners(context)
    features.set(feature, enabled)
    check(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
      .putStringSet("owners", features.persistentKeys())
      .commit()) { "Could not persist runtime owners" }
    Log.i("DigitalBrainRuntime", "owner=${feature.key} enabled=$enabled")
    refresh(context, enabled)
  }

  @JvmStatic fun refresh(context: Context, allowStart: Boolean = true) {
    if (service != null && service?.foregroundTypes != 0) {
      handler.post { service?.refresh() }
    } else if (allowStart && owners(context).isNotEmpty()) {
      try {
        ContextCompat.startForegroundService(context, Intent(context, DigitalBrainRuntimeService::class.java))
      } catch (error: RuntimeException) {
        lastError = "Foreground start rejected: ${error.javaClass.simpleName}"
        Log.w("DigitalBrainRuntime", lastError, error)
        throw error
      }
    }
  }

  fun status(context: Context): Map<String, Any?> = mapOf(
    "active" to (service != null && service?.foregroundTypes != 0),
    "owners" to owners(context).map { it.key }.sorted(),
    "locationActive" to (service?.locationActive == true),
    "startedAtMs" to service?.startedAtMs,
    "lastNativeTickAtMs" to service?.lastTickAtMs,
    "nativeTickCount" to (service?.tickCount ?: 0L),
    "workRequestCount" to RuntimeWorkService.requestCount,
    "lastWorkDurationMs" to RuntimeWorkService.lastDurationMs,
    "lastError" to lastError,
    "foregroundTypes" to (service?.foregroundTypes ?: 0),
  )
}
