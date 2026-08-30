package expo.modules.digitalbrainglassesalerts

import android.content.ComponentName
import android.service.notification.NotificationListenerService
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class GlassesAlertsModule : Module() {
  private fun context() = appContext.reactContext
    ?: throw IllegalStateException("Android application context is unavailable.")

  override fun definition() = ModuleDefinition {
    Name("DigitalBrainGlassesAlerts")

    AsyncFunction("getStatus") {
      val context = context()
      val config = GlassesAlertSettings.config(context)
      val device = GlassesAlertSettings.findGlassesAudioDevice(context)
      mapOf(
        "notificationAccessGranted" to GlassesAlertSettings.isNotificationAccessGranted(context),
        "phoneStatePermissionGranted" to GlassesAlertSettings.isPhoneStatePermissionGranted(context),
        "phoneActivelyInUse" to GlassesAlertSettings.isPhoneActivelyInUse(context),
        "glassesAudioAvailable" to (device != null),
        "glassesAudioDeviceName" to device?.productName?.toString(),
        "settings" to mapOf(
          "enabled" to config.enabled,
          "selectedPackages" to config.selectedPackages.sorted(),
          "expectedAudioDeviceName" to config.expectedAudioDeviceName,
        ),
      )
    }

    AsyncFunction("getLaunchableApps") {
      GlassesAlertSettings.launchableApps(context())
    }

    AsyncFunction("saveSettings") { enabled: Boolean, selectedPackages: List<String> ->
      val config = GlassesAlertSettings.save(context(), enabled, selectedPackages)
      mapOf(
        "enabled" to config.enabled,
        "selectedPackages" to config.selectedPackages.sorted(),
        "expectedAudioDeviceName" to config.expectedAudioDeviceName,
      )
    }

    AsyncFunction("setExpectedGlassesAudioDeviceName") { deviceName: String? ->
      GlassesAlertSettings.setExpectedAudioDeviceName(context(), deviceName)
    }

    AsyncFunction("refreshNotificationListener") {
      GlassesAlertNotificationListenerService.refreshPhoneStateListener()
      NotificationListenerService.requestRebind(
        ComponentName(context(), GlassesAlertNotificationListenerService::class.java),
      )
    }

    AsyncFunction("openNotificationAccessSettings") {
      context().startActivity(GlassesAlertSettings.notificationAccessIntent())
    }

    AsyncFunction("playTestAlert") {
      GlassesAlertPlayback.playNotificationAlert(context())
    }

    AsyncFunction("playTestCallAlert") {
      GlassesAlertPlayback.playCallPreview(context())
    }
  }
}
