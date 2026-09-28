package expo.modules.digitalbrainglassesalerts

import android.content.Context
import com.mentra.bluetoothsdk.DeviceManager
import com.mentra.bluetoothsdk.sgcs.MentraLive

/** Default-on firmware experiment. Never discards incoming PCM based on VAD events. */
internal object GlassesWakeVad {
  private const val PREFERENCES = "digital_brain_wake"
  private const val ENABLED = "glasses_vad_experiment"
  private const val DETECTION_ENABLED = "wake_detection_processing_enabled"
  private const val LISTENING_ENABLED = "continuous_glasses_listening_enabled"

  fun enabled(context: Context): Boolean =
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).getBoolean(ENABLED, true)

  fun save(context: Context, enabled: Boolean) {
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
      .putBoolean(ENABLED, enabled).apply()
  }

  fun detectionEnabled(context: Context): Boolean =
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).getBoolean(DETECTION_ENABLED, true)

  fun saveDetectionEnabled(context: Context, enabled: Boolean) {
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
      .putBoolean(DETECTION_ENABLED, enabled).apply()
  }

  fun listeningEnabled(context: Context): Boolean =
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).getBoolean(LISTENING_ENABLED, true)

  fun saveListeningEnabled(context: Context, enabled: Boolean) {
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
      .putBoolean(LISTENING_ENABLED, enabled).apply()
  }

  fun apply(context: Context, detecting: Boolean) {
    (DeviceManager.getInstance().sgc as? MentraLive)?.setWakeVadEnabled(detecting && enabled(context))
  }

  fun stats(context: Context): Map<String, Any> {
    val live = DeviceManager.getInstance().sgc as? MentraLive
    return mapOf(
      "enabled" to enabled(context),
      "detectionEnabled" to detectionEnabled(context),
      "continuousListeningEnabled" to listeningEnabled(context),
      "supportedDevice" to (live != null),
      "micRecovery" to (live?.micRecoveryStats() ?: emptyMap<String, Any>()),
    )
  }
}
