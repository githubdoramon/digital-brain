package expo.modules.digitalbrainruntime

import expo.modules.kotlin.functions.Coroutine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class DigitalBrainRuntimeModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("DigitalBrainRuntime")

    AsyncFunction("setRuntimeLocationEnabled") { enabled: Boolean ->
      DigitalBrainRuntime.setFeature(context(), RuntimeFeature.LOCATION.key, enabled)
    }
    AsyncFunction("configureRuntimeLocationUploader") { apiBaseUrl: String, googleWebClientId: String ->
      RuntimeLocationUploadConfigStore.save(context(), apiBaseUrl, googleWebClientId)
    }
    AsyncFunction("setGlassesSignedIn") Coroutine { signedIn: Boolean ->
      withContext(Dispatchers.Main) { RuntimeGlasses.setSignedIn(context(), signedIn) }
    }
    AsyncFunction("setGlassesEnabled") Coroutine { enabled: Boolean ->
      withContext(Dispatchers.Main) {
        check(!enabled || RuntimeGlasses.signedIn(context())) { "Sign in first" }
        check(!enabled || RuntimeGlasses.permitted(context())) { "Grant Bluetooth permissions first" }
        DigitalBrainRuntime.setFeature(context(), RuntimeFeature.GLASSES.key, enabled)
        if (!enabled) RuntimeGlasses.stop()
      }
    }
    AsyncFunction("syncGlassesMedia") { GlassesMediaWorker.enqueue(context(), manual = true) }
    AsyncFunction("getGlassesRecordingStatus") Coroutine { -> withContext(Dispatchers.Main) {
      GlassesRecording.snapshot(context()) + mapOf("playback" to GlassesRecordingPlayback.snapshot())
    } }
    AsyncFunction("startGlassesRecording") Coroutine { baseUri: String -> withContext(Dispatchers.Main) {
      check(appContext.currentActivity?.hasWindowFocus() == true) { "Open Recordings to start recording" }
      GlassesRecording.start(context(), baseUri)
    } }
    AsyncFunction("stopGlassesRecording") Coroutine { -> withContext(Dispatchers.Main) { GlassesRecording.stop() } }
    AsyncFunction("retryGlassesRecordingSave") Coroutine { baseUri: String -> withContext(Dispatchers.Main) { GlassesRecording.retry(context(), baseUri) } }
    AsyncFunction("playGlassesRecording") Coroutine { uri: String -> withContext(Dispatchers.Main) { GlassesRecordingPlayback.play(context(), uri) } }
    AsyncFunction("seekGlassesRecording") Coroutine { ms: Int -> withContext(Dispatchers.Main) { GlassesRecordingPlayback.seek(ms) } }
    AsyncFunction("stopGlassesRecordingPlayback") Coroutine { -> withContext(Dispatchers.Main) { GlassesRecordingPlayback.stop() } }
    AsyncFunction("getGlassesStatus") Coroutine { ->
      withContext(Dispatchers.Main) { RuntimeGlasses.snapshot(context()) }
    }
    AsyncFunction("scanGlasses") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.scan() } }
    AsyncFunction("selectGlasses") Coroutine { id: String -> withContext(Dispatchers.Main) { RuntimeGlasses.select(id) } }
    AsyncFunction("connectGlasses") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.connectNow() } }
    AsyncFunction("forgetGlasses") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.forget(context()) } }
    AsyncFunction("checkGlassesFirmware") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.checkFirmware() } }
    AsyncFunction("updateGlassesFirmware") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.update() } }
    AsyncFunction("scanGlassesWifi") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.scanWifi() } }
    AsyncFunction("configureGlassesWifi") Coroutine { ssid: String, password: String ->
      withContext(Dispatchers.Main) { RuntimeGlasses.configureWifi(ssid, password) }
    }
    AsyncFunction("getGlassesDiagnostics") Coroutine { ->
      val c = context()
      val connection = withContext(Dispatchers.Main) { RuntimeGlasses.diagnostics(c) }
      connection + withContext(Dispatchers.IO) { mapOf("mediaLog" to GlassesMediaDiagnostics.snapshot(c)) }
    }
    AsyncFunction("getGlassesAlertsStatus") Coroutine { -> withContext(Dispatchers.Main) { GlassesAlertSettings.status(context()) } }
    AsyncFunction("getGlassesAlertApps") { GlassesAlertSettings.apps(context()) }
    AsyncFunction("saveGlassesAlerts") Coroutine { notifications: Boolean, calls: Boolean, packages: List<String>, chime: Int, call: Int ->
      withContext(Dispatchers.Main) { GlassesAlertSettings.save(context(), notifications, calls, packages, chime, call) }
    }
    AsyncFunction("openGlassesNotificationAccess") { GlassesAlertSettings.openAccess(context()) }
    AsyncFunction("openGlassesSoundSettings") { GlassesAlertSettings.openSound(context()) }
    AsyncFunction("testGlassesAlert") Coroutine { call: Boolean -> withContext(Dispatchers.Main) {
      check(GlassesAlertPlayback.preview(context(), call)) { GlassesAlertPlayback.lastError ?: "Could not play glasses alert" }
    } }
    AsyncFunction("stopGlassesAlertTest") Coroutine { -> withContext(Dispatchers.Main) { GlassesAlertPlayback.stopPreview() } }
    AsyncFunction("refreshGlassesAlerts") Coroutine { -> withContext(Dispatchers.Main) { GlassesAlertNotificationListenerService.refreshSettings() } }
    AsyncFunction("getAppRuntimeStatus") { DigitalBrainRuntime.status(context()) }
    AsyncFunction("getRuntimeEnergyDiagnostics") { RuntimeEnergyDiagnostics.sample(context()) }
  }

  private fun context() = requireNotNull(appContext.reactContext).applicationContext
}
