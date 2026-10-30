package expo.modules.digitalbrainruntime

import expo.modules.kotlin.functions.Coroutine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
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
    OnActivityEntersForeground {
      kotlinx.coroutines.CoroutineScope(Dispatchers.Main).launch {
        DigitalBrainRuntime.activityVisible = true
        appContext.reactContext?.applicationContext?.let { GlassesListening.resumeForeground(it) }
      }
    }
    OnActivityEntersBackground {
      DigitalBrainRuntime.activityVisible = false
      kotlinx.coroutines.CoroutineScope(Dispatchers.Main).launch { GlassesReplyTest.cancel() }
    }
    AsyncFunction("testGlassesReply") Coroutine { text: String ->
      check(appContext.currentActivity?.hasWindowFocus() == true) { "Open the app to test a spoken reply" }
      withContext(Dispatchers.IO) { GlassesReplyTest.run(context(), text) }
    }
    AsyncFunction("cancelGlassesReplyTest") Coroutine { -> withContext(Dispatchers.Main) { GlassesReplyTest.cancel() } }
    AsyncFunction("getGlassesSpeechTestStatus") Coroutine { -> withContext(Dispatchers.IO) { SpeechEvalStore.snapshot(context()) } }
    AsyncFunction("startGlassesSpeechTest") Coroutine { folder: String, models: List<String>, repeats: Int ->
      check(appContext.currentActivity?.hasWindowFocus() == true) { "Open the app to start a comparison" }
      withContext(Dispatchers.IO) {
        val c = context()
        check(RuntimeGlasses.signedIn(c)) { "Sign in first" }
        check(!SpeechEvalWorker.busy(c)) { "Pause the current test before starting another" }
        val owner = SpeechEvalStore.owner(c)
        SpeechEvalStore.import(c, owner, folder, models, repeats)
        SpeechEvalWorker.enqueue(c, owner)
      }
    }
    AsyncFunction("resumeGlassesSpeechTest") Coroutine { -> withContext(Dispatchers.IO) {
      val c = context(); check(RuntimeGlasses.signedIn(c)) { "Sign in first" }
      check(!SpeechEvalWorker.busy(c)) { "The test is still stopping. Try again shortly." }
      val owner = SpeechEvalStore.owner(c)
      val value = checkNotNull(SpeechEvalStore.read(c, owner)) { "Choose a dataset first" }
      check(SpeechEvalServerCandidate.supportsRun(value)) { "Start a new recognition test" }
      check(value.runState() != SpeechEvalRunState.COMPLETE) { "This comparison is complete" }
      value.put("state", SpeechEvalRunState.READY.key).put("message", "Waiting to continue")
      SpeechEvalStore.save(c, owner, value); SpeechEvalWorker.enqueue(c, owner)
    } }
    AsyncFunction("pauseGlassesSpeechTest") Coroutine { ->
      withContext(Dispatchers.IO) { SpeechEvalWorker.pause(context()) }
      withContext(Dispatchers.Main) {
        if (!SpeechEvalWorker.running) {
          GlassesListening.endComparison()
          if (DigitalBrainRuntime.activityVisible) GlassesListening.resumeForeground(context())
        }
      }
    }
    AsyncFunction("exportGlassesSpeechTest") Coroutine { baseUri: String -> withContext(Dispatchers.IO) {
      val c = context(); SpeechEvalStore.export(c, SpeechEvalStore.owner(c), baseUri)
    } }
    AsyncFunction("getGlassesListeningStatus") Coroutine { -> withContext(Dispatchers.Main) { GlassesListening.snapshot(context()) } }
    AsyncFunction("setGlassesListening") Coroutine { enabled: Boolean, baseUri: String? ->
      if (enabled) withContext(Dispatchers.IO) { GlassesWakeStore.validateBase(context(), checkNotNull(baseUri)) }
      withContext(Dispatchers.Main) {
      check(!enabled || appContext.currentActivity?.hasWindowFocus() == true) { "Open the app to start listening" }
      GlassesListening.configure(context(), enabled, baseUri)
    } }
    AsyncFunction("setGlassesListeningMinimumVolume") Coroutine { value: Int -> withContext(Dispatchers.Main) { GlassesListening.setMinimumVolume(context(), value) } }
    AsyncFunction("retryGlassesWakeSaves") Coroutine { baseUri: String -> withContext(Dispatchers.IO) { GlassesListening.retrySaves(context(), baseUri) } }
    AsyncFunction("testGlassesLight") Coroutine { color: String, pattern: String -> withContext(Dispatchers.Main) {
      check(appContext.currentActivity?.hasWindowFocus() == true) { "Open the app to test the light" }
      RuntimeGlasses.testLight(context(), color, pattern)
    } }
    AsyncFunction("endGlassesLightTest") Coroutine { -> withContext(Dispatchers.Main) { RuntimeGlasses.endLightTest() } }
    AsyncFunction("reportGlassesLightTest") Coroutine { attempt: Int, visible: Boolean -> withContext(Dispatchers.Main) {
      GlassesLedDiagnostics.observation(context(), attempt, visible)
      RuntimeGlasses.endLightTest()
    } }
    AsyncFunction("syncGlassesMedia") { GlassesMediaWorker.enqueue(context(), manual = true) }
    AsyncFunction("getGlassesRecordingStatus") Coroutine { -> withContext(Dispatchers.Main) {
      GlassesRecording.snapshot(context()) + mapOf("playback" to GlassesRecordingPlayback.snapshot())
    } }
    AsyncFunction("startGlassesRecording") Coroutine { baseUri: String -> withContext(Dispatchers.Main) {
      check(appContext.currentActivity?.hasWindowFocus() == true) { "Open Recordings to start recording" }
      GlassesRecording.start(context(), baseUri)
    } }
    AsyncFunction("stopGlassesRecording") Coroutine { -> withContext(Dispatchers.Main) { GlassesRecording.stop() } }
    AsyncFunction("retryGlassesRecordingSave") Coroutine { baseUri: String, id: String? -> withContext(Dispatchers.Main) { GlassesRecording.retry(context(), baseUri, id) } }
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
