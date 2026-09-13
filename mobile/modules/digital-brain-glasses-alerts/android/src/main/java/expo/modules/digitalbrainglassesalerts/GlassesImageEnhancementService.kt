package expo.modules.digitalbrainglassesalerts

import android.content.Context

/** Compatibility facade: automatic capture and wake listening share the app service. */
object GlassesImageEnhancementService {
  fun start(context: Context, intervalMinutes: Int, scheduleCount: Int) {
    DigitalBrainRuntime.owners(context)
    DigitalBrainRuntime.captureIntervalMinutes = intervalMinutes
    DigitalBrainRuntime.captureScheduleCount = scheduleCount
    DigitalBrainRuntime.setFeature(context, RuntimeFeature.CAPTURE.key, true)
  }
  fun stop(context: Context) = DigitalBrainRuntime.setFeature(context, RuntimeFeature.CAPTURE.key, false)
  fun startWakeRuntime(context: Context) = DigitalBrainRuntime.setFeature(context, RuntimeFeature.WAKE.key, true)
  fun stopWakeRuntime(context: Context) = DigitalBrainRuntime.setFeature(context, RuntimeFeature.WAKE.key, false)
  fun status(context: Context) = DigitalBrainRuntime.status(context) + mapOf(
    "active" to (DigitalBrainRuntime.service != null && RuntimeFeature.CAPTURE in DigitalBrainRuntime.owners(context)),
  )
  fun runtimeStatus(context: Context) = DigitalBrainRuntime.status(context) + mapOf(
    "wakeListeningRequested" to (RuntimeFeature.WAKE in DigitalBrainRuntime.owners(context)),
    "automaticCaptureActive" to (RuntimeFeature.CAPTURE in DigitalBrainRuntime.owners(context)),
  )
}
