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
  private var wakeSpotter: V8KeywordSpotter? = null
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
    Events(
      "onImageEnhancementForegroundTick",
      "onSpeechPlaybackStarted",
      "onSpeechPlaybackProgress",
      "onSpeechPlaybackFinished",
    )

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
      GlassesImageEnhancementService.status(context())
    }

    AsyncFunction("startGlassesWakeRuntime") {
      GlassesImageEnhancementService.startWakeRuntime(context())
    }

    AsyncFunction("stopGlassesWakeRuntime") {
      GlassesImageEnhancementService.stopWakeRuntime(context())
    }

    AsyncFunction("getGlassesRuntimeForegroundServiceStatus") {
      GlassesImageEnhancementService.runtimeStatus(context())
    }

    AsyncFunction("initializeV8WakeSpotter") {
      synchronized(this@GlassesAlertsModule) {
        if (wakeSpotter == null) wakeSpotter = V8KeywordSpotter(context())
      }
    }

    AsyncFunction("acceptV8WakePcm16") { pcmBase64: String ->
      synchronized(this@GlassesAlertsModule) {
        (wakeSpotter ?: throw IllegalStateException("V8 wake spotter is not initialized"))
          .acceptPcm16Base64(pcmBase64)
      }
    }

    AsyncFunction("getV8WakeSpotterStats") {
      synchronized(this@GlassesAlertsModule) { wakeSpotter?.stats() }
    }

    AsyncFunction("resetV8WakeSpotter") {
      synchronized(this@GlassesAlertsModule) { wakeSpotter?.reset() }
    }

    AsyncFunction("releaseV8WakeSpotter") {
      synchronized(this@GlassesAlertsModule) {
        wakeSpotter?.release()
        wakeSpotter = null
      }
    }

    AsyncFunction("playSpeechAudio") { commandId: String, fileUri: String ->
      GlassesAlertPlayback.playSpeechAudio(
        context(),
        commandId,
        fileUri,
        onStarted = { telemetry ->
          sendEvent(
            "onSpeechPlaybackStarted",
            mapOf(
              "commandId" to commandId,
              "expectedDeviceId" to telemetry.expectedDeviceId,
              "expectedDeviceName" to telemetry.expectedDeviceName,
              "expectedDeviceType" to telemetry.expectedDeviceType,
              "routedDeviceId" to telemetry.routedDeviceId,
              "routedDeviceName" to telemetry.routedDeviceName,
              "routedDeviceType" to telemetry.routedDeviceType,
              "routeVerified" to telemetry.routeVerified,
              "audioFocusResult" to telemetry.audioFocusResult,
              "audioFocusGranted" to telemetry.audioFocusGranted,
              "runtimeForegroundTypes" to telemetry.runtimeForegroundTypes,
              "activityVisible" to telemetry.activityVisible,
              "outputStreamVolume" to telemetry.outputStreamVolume,
              "outputStreamVolumeMax" to telemetry.outputStreamVolumeMax,
              "outputStreamMuted" to telemetry.outputStreamMuted,
              "playerDurationMs" to telemetry.playerDurationMs,
              "playerPositionMs" to telemetry.playerPositionMs,
              "playerIsPlaying" to telemetry.playerIsPlaying,
              "playerAudioSessionId" to telemetry.playerAudioSessionId,
              "playerGain" to telemetry.playerGain,
            ),
          )
        },
        onProgress = { telemetry ->
          sendEvent(
            "onSpeechPlaybackProgress",
            mapOf(
              "commandId" to commandId,
              "expectedDeviceId" to telemetry.expectedDeviceId,
              "expectedDeviceName" to telemetry.expectedDeviceName,
              "expectedDeviceType" to telemetry.expectedDeviceType,
              "routedDeviceId" to telemetry.routedDeviceId,
              "routedDeviceName" to telemetry.routedDeviceName,
              "routedDeviceType" to telemetry.routedDeviceType,
              "routeVerified" to telemetry.routeVerified,
              "audioFocusResult" to telemetry.audioFocusResult,
              "audioFocusGranted" to telemetry.audioFocusGranted,
              "runtimeForegroundTypes" to telemetry.runtimeForegroundTypes,
              "activityVisible" to telemetry.activityVisible,
              "outputStreamVolume" to telemetry.outputStreamVolume,
              "outputStreamVolumeMax" to telemetry.outputStreamVolumeMax,
              "outputStreamMuted" to telemetry.outputStreamMuted,
              "playerDurationMs" to telemetry.playerDurationMs,
              "playerPositionMs" to telemetry.playerPositionMs,
              "playerIsPlaying" to telemetry.playerIsPlaying,
              "playerAudioSessionId" to telemetry.playerAudioSessionId,
              "playerGain" to telemetry.playerGain,
            ),
          )
        },
        onFinished = { result ->
          val telemetry = result.telemetry
          sendEvent(
            "onSpeechPlaybackFinished",
            mapOf(
              "commandId" to commandId,
              "status" to result.status,
              "durationMs" to result.durationMs,
              "error" to result.error,
              "expectedDeviceId" to telemetry.expectedDeviceId,
              "expectedDeviceName" to telemetry.expectedDeviceName,
              "expectedDeviceType" to telemetry.expectedDeviceType,
              "routedDeviceId" to telemetry.routedDeviceId,
              "routedDeviceName" to telemetry.routedDeviceName,
              "routedDeviceType" to telemetry.routedDeviceType,
              "routeVerified" to telemetry.routeVerified,
              "audioFocusResult" to telemetry.audioFocusResult,
              "audioFocusGranted" to telemetry.audioFocusGranted,
              "runtimeForegroundTypes" to telemetry.runtimeForegroundTypes,
              "activityVisible" to telemetry.activityVisible,
              "outputStreamVolume" to telemetry.outputStreamVolume,
              "outputStreamVolumeMax" to telemetry.outputStreamVolumeMax,
              "outputStreamMuted" to telemetry.outputStreamMuted,
              "playerDurationMs" to telemetry.playerDurationMs,
              "playerPositionMs" to telemetry.playerPositionMs,
              "playerIsPlaying" to telemetry.playerIsPlaying,
              "playerAudioSessionId" to telemetry.playerAudioSessionId,
              "playerGain" to telemetry.playerGain,
            ),
          )
        },
      )
    }

    AsyncFunction("stopSpeechAudio") { commandId: String? ->
      mapOf("stopped" to GlassesAlertPlayback.stopSpeechAudio(commandId))
    }

    AsyncFunction("setRuntimeLocationEnabled") { enabled: Boolean ->
      DigitalBrainRuntime.setFeature(context(), RuntimeFeature.LOCATION.key, enabled)
    }
    AsyncFunction("getAppRuntimeStatus") { DigitalBrainRuntime.status(context()) }
    AsyncFunction("getRuntimeEnergyDiagnostics") { RuntimeEnergyDiagnostics.sample(context()) }
    AsyncFunction("completeRuntimeWork") { token: String -> RuntimeWorkService.complete(token) }
    AsyncFunction("readRuntimeLocations") { RuntimeLocationStore.samples(context()) }
    AsyncFunction("acknowledgeRuntimeLocations") { ids: List<String> ->
      RuntimeLocationStore.acknowledge(context(), ids.toSet())
    }
    OnActivityEntersForeground {
      DigitalBrainRuntime.activityVisible = true
      runCatching { DigitalBrainRuntime.refresh(context()) }
    }
    OnActivityEntersBackground { DigitalBrainRuntime.activityVisible = false }

    OnDestroy {
      GlassesAlertPlayback.stopSpeechAudio(null)
      synchronized(this@GlassesAlertsModule) {
        wakeSpotter?.release()
        wakeSpotter = null
      }
      if (activeModule?.get() === this@GlassesAlertsModule) activeModule = null
    }
  }
}
