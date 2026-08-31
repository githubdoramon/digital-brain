package expo.modules.digitalbrainglassesalerts

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.service.notification.NotificationListenerService
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.lang.ref.WeakReference

class GlassesAlertsModule : Module() {
  companion object {
    private var activeModule: WeakReference<GlassesAlertsModule>? = null

    fun emitImageEnhancementForegroundTick() {
      activeModule?.get()?.sendEvent(
        "onImageEnhancementForegroundTick",
        mapOf("timestampMs" to System.currentTimeMillis()),
      )
    }
  }

  private fun context() = appContext.reactContext
    ?: throw IllegalStateException("Android application context is unavailable.")

  override fun definition() = ModuleDefinition {
    Name("DigitalBrainGlassesAlerts")
    Events("onImageEnhancementForegroundTick")

    OnCreate {
      activeModule = WeakReference(this@GlassesAlertsModule)
    }

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
      GlassesAlertPlayback.playNotificationPreview(context())
    }

    AsyncFunction("playTestCallAlert") {
      GlassesAlertPlayback.playCallPreview(context())
    }

    AsyncFunction("startImageEnhancementForegroundService") { intervalMinutes: Int, scheduleCount: Int ->
      GlassesImageEnhancementService.start(
        context(),
        intervalMinutes.coerceAtLeast(1),
        scheduleCount.coerceAtLeast(1),
      )
    }

    AsyncFunction("stopImageEnhancementForegroundService") {
      GlassesImageEnhancementService.stop(context())
    }

    AsyncFunction("getImageEnhancementDeviceHealth") {
      val context = context()
      val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
      val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
      val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
      val batteryPercent = if (level >= 0 && scale > 0) level * 100.0 / scale else null
      val batteryStatus = battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
      val charging = when (batteryStatus) {
        BatteryManager.BATTERY_STATUS_CHARGING,
        BatteryManager.BATTERY_STATUS_FULL,
        -> true
        BatteryManager.BATTERY_STATUS_DISCHARGING,
        BatteryManager.BATTERY_STATUS_NOT_CHARGING,
        -> false
        else -> null
      }
      val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
      val thermalStatus = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        powerManager.currentThermalStatus
      } else {
        null
      }
      val thermalStatusLabel = when (thermalStatus) {
        PowerManager.THERMAL_STATUS_NONE -> "none"
        PowerManager.THERMAL_STATUS_LIGHT -> "light"
        PowerManager.THERMAL_STATUS_MODERATE -> "moderate"
        PowerManager.THERMAL_STATUS_SEVERE -> "severe"
        PowerManager.THERMAL_STATUS_CRITICAL -> "critical"
        PowerManager.THERMAL_STATUS_EMERGENCY -> "emergency"
        PowerManager.THERMAL_STATUS_SHUTDOWN -> "shutdown"
        else -> "unavailable"
      }
      mapOf(
        "batteryPercent" to batteryPercent,
        "charging" to charging,
        "thermalStatus" to thermalStatus,
        "thermalStatusLabel" to thermalStatusLabel,
        "appMemoryBytes" to Debug.getPss() * 1024L,
      )
    }

    AsyncFunction("getImageEnhancementForegroundServiceStatus") {
      GlassesImageEnhancementService.status()
    }

    OnDestroy {
      if (activeModule?.get() === this@GlassesAlertsModule) activeModule = null
    }
  }
}
