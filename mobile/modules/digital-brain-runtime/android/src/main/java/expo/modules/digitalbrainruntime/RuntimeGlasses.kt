package expo.modules.digitalbrainruntime

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import com.mentra.bluetoothsdk.*
import kotlinx.coroutines.*

/** Single native connection owner. All state mutations run on the main looper. */
object RuntimeGlasses {
  private const val PREFS = "digital_brain_glasses"
  private val handler = Handler(Looper.getMainLooper())
  private var sdk: MentraBluetoothSdk? = null
  private var mediaTeardown: Job? = null
  private var generation = 0L
  private var scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
  private var context: Context? = null
  private var receiver: BroadcastReceiver? = null
  private var state: MentraBluetoothState? = null
  private var devices = linkedMapOf<String, Device>()
  private val retries = GlassesRetryPolicy()
  private var retry: Runnable? = null
  private var deadline: Runnable? = null
  private var stable: Runnable? = null
  private var nextRetryAt: Long? = null
  private var attempting = false
  private var pendingPair: Device? = null
  private var discovery = false
  private var firmwareBusy = false
  private var updateActive = false
  private var updateAvailable: Boolean? = null
  private var updateProgress = 0
  private var updateStatus = "not_checked"
  private var error: String? = null
  private var firmwareError: String? = null
  private val trail = ArrayDeque<Map<String, Any?>>()

  private fun prefs(c: Context) = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
  fun signedIn(c: Context) = prefs(c).getBoolean("signed_in", false)
  fun enabled(c: Context) = RuntimeFeature.GLASSES in DigitalBrainRuntime.owners(c)
  fun permitted(c: Context): Boolean = if (Build.VERSION.SDK_INT >= 31) {
    listOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN).all {
      ContextCompat.checkSelfPermission(c, it) == PackageManager.PERMISSION_GRANTED
    }
  } else ContextCompat.checkSelfPermission(c, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
  private fun bluetoothOn(c: Context) = permitted(c) &&
    c.getSystemService(BluetoothManager::class.java)?.adapter?.isEnabled == true

  fun setSignedIn(c: Context, value: Boolean) {
    check(prefs(c).edit().putBoolean("signed_in", value).commit())
    if (!value) stop()
    DigitalBrainRuntime.refresh(c, allowStart = value)
  }

  fun start(c: Context) {
    if (mediaTeardown != null || sdk != null || !enabled(c) || !signedIn(c) || !permitted(c)) return
    val runtimeContext = c.applicationContext
    context = runtimeContext
    updateActive = prefs(c).getBoolean("update_active", false)
    scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val sessionGeneration = ++generation
    sdk = MentraBluetoothSdk.create(c, MentraBluetoothSdkConfig(analytics = BluetoothSdkAnalyticsConfig.disabled()),
      object : MentraBluetoothSdkListener {
        override fun onStateChanged(value: MentraBluetoothState) { if (generation == sessionGeneration) onState(value) }
        override fun onDeviceDiscovered(device: Device) {
          if (generation != sessionGeneration) return
          if (device.model == DeviceModel.MENTRA_LIVE) devices[device.id] = device
        }
        override fun onScanStopped(reason: ScanStopReason) {
          if (generation != sessionGeneration) return
          if (discovery) { discovery = false; record("scan_finished"); scheduleRetry() }
        }
        override fun onError(value: BluetoothError) { if (generation == sessionGeneration) { error = value.code; record("sdk_error", value.code) } }
        override fun onMicPcm(event: MicPcmEvent) { if (generation == sessionGeneration) GlassesRecording.pcm(event) }
        override fun onPhotoStatus(event: PhotoStatusEvent) { if (generation == sessionGeneration) scheduleMediaSync() }
        override fun onVideoRecordingStatus(event: VideoRecordingStatusEvent) { if (generation == sessionGeneration) scheduleMediaSync() }
        override fun onButtonPress(event: ButtonPressEvent) { if (generation == sessionGeneration && event.pressType == "short") scheduleMediaSync() }
        override fun onOtaStatus(event: OtaStatusEvent) {
          if (generation != sessionGeneration) return
          updateProgress = event.overallPercent.coerceIn(0, 100)
          updateStatus = event.status
          updateActive = event.status !in setOf("complete", "failed", "idle", "up_to_date")
          context?.let { prefs(it).edit().putBoolean("update_active", updateActive).apply() }
          record("firmware_progress", event.status)
          if (!updateActive) {
            firmwareError = if (event.status == "failed") "Glasses reported an update failure" else null
            if (event.status == "complete") updateAvailable = false
            scheduleRetry()
          }
        }
      })
    // Restore only our explicitly selected pair. SDK store defaults are session-local.
    savedDevice(c)?.let { sdk?.setDefaultDevice(it) }
    receiver = object : BroadcastReceiver() {
      override fun onReceive(c: Context, intent: Intent) {
        if (generation != sessionGeneration || sdk == null) return
        when (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1)) {
          BluetoothAdapter.STATE_ON -> { cancelRetry(); connectNow() }
          BluetoothAdapter.STATE_OFF -> {
            cancelRetry(); cancelAttempt(); error = "Bluetooth is off"; record("bluetooth_off")
          }
        }
      }
    }.also {
      // Android registrations belong to the registering Context, including during service teardown.
      ContextCompat.registerReceiver(runtimeContext, it, IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED), ContextCompat.RECEIVER_EXPORTED)
    }
    record("runtime_started")
    connectNow()
    GlassesAlertNotificationListenerService.refreshSettings()
  }

  fun stop() {
    GlassesRecording.stop(GlassesRecordingStop.RUNTIME_STOPPED)
    context?.let { GlassesMediaWorker.cancel(it) }
    GlassesAlertPlayback.stop()
    GlassesAlertNotificationListenerService.refreshSettings()
    if (sdk == null && receiver == null) return
    val old = sdk
    generation++
    sdk = null // Ignore synchronous/late state callbacks from teardown.
    cancelRetry(); cancelAttempt()
    stable?.let(handler::removeCallbacks); stable = null
    scope.cancel()
    mediaDebounce?.let(handler::removeCallbacks); mediaDebounce = null
    val registeredReceiver = receiver
    receiver = null // Claim cleanup before unregistering; a repeated stop has nothing to unregister.
    registeredReceiver?.let {
      try { context?.unregisterReceiver(it) }
      catch (_: IllegalArgumentException) {
        // Finish SDK cleanup even if a registration was already removed by Android.
        record("receiver_cleanup_failed", "not_registered")
      }
    }
    val c = context
    val mediaPrefs = c?.getSharedPreferences("glasses_media_status", Context.MODE_PRIVATE)
    if (old != null && c != null && mediaPrefs?.getString("owned_hotspot", null) != null) {
      // Give our hotspot a bounded shutdown before closing BLE. A rapid re-enable waits
      // for this old SDK to close so it cannot tear down the new connection owner.
      mediaTeardown = CoroutineScope(SupervisorJob() + Dispatchers.Main).launch {
        try {
          withTimeout(3000) {
            while (true) {
              try {
                old.setHotspotState(false)
                mediaPrefs.edit().remove("owned_hotspot").commit()
                break
              } catch (e: CancellationException) { throw e }
              catch (_: Exception) { delay(250) }
            }
          }
        } catch (_: Exception) { /* Persist ownership for cleanup on the next ready connection. */ }
        finally {
          old.stopScan(); old.disconnect(); old.close()
          mediaTeardown = null
          start(c)
        }
      }
    } else { old?.stopScan(); old?.disconnect(); old?.close() }
    state = null; pendingPair = null; discovery = false; firmwareBusy = false
    // Preserve an in-flight OTA marker across service restart; glasses own installation.
    context?.let { prefs(it).edit().putBoolean("update_active", updateActive).apply() }
    record("runtime_stopped")
  }

  private fun onState(value: MentraBluetoothState) {
    if (sdk == null) return
    val wasReady = state?.glasses?.ready == true
    state = value
    if (!alertsReady()) {
      GlassesRecording.stop(GlassesRecordingStop.DISCONNECTED)
      GlassesAlertPlayback.stop()
    }
    GlassesAlertNotificationListenerService.connectionChanged()
    if (value.glasses.ready && !wasReady) {
      cancelRetry(); deadline?.let(handler::removeCallbacks); deadline = null; attempting = false
      error = null; record("connected_ready")
      context?.let { GlassesMediaWorker.enqueue(it) }
      pendingPair?.let { pair ->
        context?.let { c -> check(prefs(c).edit().putString("name", pair.name).putString("address", pair.address).commit()) }
        pendingPair = null
        sdk?.setDefaultDevice(pair)
      }
      stable?.let(handler::removeCallbacks)
      stable = Runnable { retries.reset() }.also { handler.postDelayed(it, 60_000) }
      if (updateActive) reconcileUpdate() else checkFirmware()
    } else if (!value.glasses.ready) {
      stable?.let(handler::removeCallbacks); stable = null
      if (!value.glasses.connected) {
        if (wasReady) { updateAvailable = null; record("disconnected") }
        scheduleRetry()
      }
    }
  }

  private fun savedDevice(c: Context): Device? {
    val name = prefs(c).getString("name", null) ?: return null
    return Device(DeviceModel.MENTRA_LIVE, name, prefs(c).getString("address", null))
  }

  fun scan() {
    check(!updateActive) { "Wait for the firmware update to finish" }
    check(requireNotNull(context).let(::bluetoothOn)) { "Turn on Bluetooth and grant Nearby devices access" }
    discovery = true; cancelAttempt(); cancelRetry(); devices.clear()
    record("scan_started")
    try { sdk!!.scan(DeviceModel.MENTRA_LIVE, object : ScanCallback {}, 15_000L) }
    catch (e: Exception) { discovery = false; scheduleRetry(); throw e }
  }

  fun select(id: String) {
    check(!updateActive)
    val device = requireNotNull(devices[id]) { "Scan again to select these glasses" }
    discovery = true; sdk?.stopScan(); cancelAttempt(); cancelRetry(); discovery = false
    pendingPair = device
    retries.reset(); record("pair_selected"); connectNow()
  }

  fun connectNow() {
    val c = context ?: return
    if (sdk == null || !enabled(c) || !signedIn(c) || discovery || attempting || state?.glasses?.ready == true) return
    if (!bluetoothOn(c)) { error = "Bluetooth or permissions unavailable"; return }
    val device = pendingPair ?: savedDevice(c) ?: return
    cancelRetry(); attempting = true; error = null; record("connection_attempt")
    deadline = Runnable {
      deadline = null
      if (state?.glasses?.ready != true) {
        attempting = false; sdk?.cancelConnectionAttempt()
        error = "Connection timed out"; record("connection_timeout"); scheduleRetry()
      }
    }.also { handler.postDelayed(it, 45_000) }
    try { sdk!!.connect(device, ConnectOptions(saveAsDefault = pendingPair == null)) }
    catch (e: Exception) { cancelAttempt(); error = "Connection failed: ${e.javaClass.simpleName}"; scheduleRetry() }
  }

  private fun cancelAttempt() {
    deadline?.let(handler::removeCallbacks); deadline = null; attempting = false
    sdk?.cancelConnectionAttempt()
  }
  private fun cancelRetry() {
    retry?.let(handler::removeCallbacks); retry = null; nextRetryAt = null
  }
  private fun scheduleRetry() {
    val c = context ?: return
    if (sdk == null || retry != null || attempting || discovery || state?.glasses?.ready == true ||
        (pendingPair == null && savedDevice(c) == null) || !bluetoothOn(c) || !enabled(c) || !signedIn(c)) return
    val delay = retries.nextDelayMs()
    nextRetryAt = System.currentTimeMillis() + delay
    record("retry_scheduled", delay.toString())
    retry = Runnable { retry = null; nextRetryAt = null; connectNow() }.also { handler.postDelayed(it, delay) }
  }

  fun forget(c: Context) {
    check(!updateActive) { "Wait for the firmware update to finish" }
    prefs(c).edit().remove("name").remove("address").apply()
    pendingPair = null; discovery = true
    cancelAttempt(); sdk?.stopScan(); cancelRetry(); discovery = false
    sdk?.forget(); sdk?.clearDefaultDevice()
    devices.clear(); updateAvailable = null; error = null; retries.reset(); record("pair_forgotten")
  }

  fun checkFirmware() {
    if (updateActive && state?.glasses?.ready == true) { reconcileUpdate(); return }
    if (firmwareBusy || state?.glasses?.ready != true) return
    firmwareBusy = true; firmwareError = null; updateStatus = "checking"
    val current = sdk ?: return
    scope.launch {
      try {
        val available = withContext(Dispatchers.IO) { current.requestVersionInfo(); current.checkForOtaUpdate() }
        updateAvailable = available; updateStatus = if (available) "available" else "up_to_date"
        record("firmware_checked", updateStatus)
        if (available) notifyUpdate()
      } catch (e: CancellationException) { throw e }
      catch (e: Exception) {
        updateStatus = "check_failed"; firmwareError = "Firmware check failed: ${e.javaClass.simpleName}"
        record("firmware_check_failed")
      } finally { if (sdk === current) firmwareBusy = false }
    }
  }

  suspend fun update() {
    check(updateAvailable == true && state?.glasses?.ready == true && !updateActive && !firmwareBusy) { "Connect and check for an update first" }
    val current = requireNotNull(sdk)
    GlassesRecording.stop(GlassesRecordingStop.DISCONNECTED)
    updateActive = true; updateStatus = "starting"; firmwareError = null
    GlassesAlertPlayback.stop()
    context?.let { prefs(it).edit().putBoolean("update_active", true).apply() }
    try {
      val ack = withContext(Dispatchers.IO) { current.startOtaUpdate() }
      check(ack.values["status"] != "failed" && ack.values["accepted"] != false) { "Glasses rejected the firmware update" }
      record("firmware_started")
    } catch (e: Exception) {
      updateActive = false; context?.let { prefs(it).edit().putBoolean("update_active", false).apply() }; updateStatus = "failed"; firmwareError = "Could not start update: ${e.javaClass.simpleName}"
      throw e
    }
  }

  private fun reconcileUpdate() {
    val current = sdk ?: return
    scope.launch {
      try {
        val result = withContext(Dispatchers.IO) { current.sendOtaQueryStatus() }
        val status = result.status ?: "unknown"
        updateStatus = status
        updateActive = status !in setOf("complete", "failed", "idle", "up_to_date", "no_update", "available")
        context?.let { prefs(it).edit().putBoolean("update_active", updateActive).apply() }
        if (!updateActive) checkFirmware()
      } catch (e: CancellationException) { throw e }
      catch (_: Exception) { firmwareError = "Could not recover update status. Use Check for update to retry." }
    }
  }

  suspend fun scanWifi(): List<Map<String, Any>> {
    check(state?.glasses?.ready == true && !updateActive) { "Connect glasses before scanning Wi-Fi" }
    val current = requireNotNull(sdk)
    val scanGeneration = generation
    val networks = withContext(Dispatchers.IO) { current.requestWifiScan() }
    check(sdk === current && generation == scanGeneration && state?.glasses?.ready == true) { "Glasses disconnected during Wi-Fi scan. Reconnect and retry." }
    record("wifi_scanned")
    return networks.filter { it.ssid.isNotBlank() }
      .sortedByDescending { it.signalStrength }
      .distinctBy { it.ssid }
      .map { mapOf("ssid" to it.ssid, "requiresPassword" to it.requiresPassword, "signalStrength" to it.signalStrength) }
  }

  suspend fun configureWifi(ssid: String, password: String) {
    check(state?.glasses?.ready == true && !updateActive) { "Connect glasses before configuring Wi-Fi" }
    require(ssid.isNotBlank()) { "Enter the Wi-Fi network name" }
    withContext(Dispatchers.IO) { requireNotNull(sdk).sendWifiCredentials(ssid, password) }
    record("wifi_configured")
  }

  private fun notifyUpdate() {
    val c = context ?: return
    val manager = c.getSystemService(android.app.NotificationManager::class.java)
    manager.createNotificationChannel(android.app.NotificationChannel("glasses_updates", "Glasses updates", android.app.NotificationManager.IMPORTANCE_DEFAULT))
    val intent = c.packageManager.getLaunchIntentForPackage(c.packageName) ?: return
    val pending = android.app.PendingIntent.getActivity(c, 4313, intent, android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE)
    try { manager.notify(4313, androidx.core.app.NotificationCompat.Builder(c, "glasses_updates")
      .setSmallIcon(android.R.drawable.stat_sys_download).setContentTitle("Glasses update available")
      .setContentText("Open Settings → Glasses to review and install.").setContentIntent(pending).setAutoCancel(true).build()) }
    catch (_: SecurityException) { /* Availability remains visible in Settings without notification permission. */ }
  }

  private fun record(event: String, detail: String? = null) {
    if (trail.size >= 100) trail.removeFirst()
    trail.addLast(mapOf("atMs" to System.currentTimeMillis(), "event" to event, "detail" to detail))
  }

  private var mediaDebounce: Runnable? = null
  private fun scheduleMediaSync() {
    mediaDebounce?.let(handler::removeCallbacks)
    mediaDebounce = Runnable {
      mediaDebounce = null
      context?.let { if (alertsReady()) GlassesMediaWorker.enqueue(it) }
    }.also { handler.postDelayed(it, 5000) }
  }
  fun mediaDevice(): GlassesMediaDevice? {
    if (!alertsReady()) return null
    val glasses = state?.glasses as? GlassesRuntimeState.Connected ?: return null
    val c = context ?: return null
    val identity = prefs(c).getString("address", null) ?: prefs(c).getString("name", null) ?: return null
    return GlassesMediaDevice(sdk!!, GlassesMediaStore.hash(identity), (glasses.wifi as? WifiStatus.Connected)?.localIp, glasses.hotspot)
  }
  fun setRecordingMic(enabled: Boolean) {
    if (enabled) check(alertsReady()) { "Glasses microphone is unavailable" }
    if (enabled) sdk?.setVoiceActivityDetectionEnabled(false)
    sdk?.setMicState(enabled, useGlassesMic = true, sendTranscript = false, sendLc3Data = false)
  }
  fun alertsReady() = sdk != null && state?.glasses?.ready == true && !updateActive
  fun alertAudioNames(c: Context): Set<String> = setOfNotNull(
    (state?.glasses as? GlassesRuntimeState.Connected)?.device?.bluetoothName,
    prefs(c).getString("name", null)
  ).map { it.trim() }.filter { it.isNotEmpty() }.toSet()

  fun snapshot(c: Context): Map<String, Any?> {
    val glasses = state?.glasses as? GlassesRuntimeState.Connected
    val wifi = glasses?.wifi as? WifiStatus.Connected
    return mapOf(
      "wifi" to mapOf("connected" to (wifi != null), "ssid" to wifi?.ssid, "address" to wifi?.localIp),
      "hotspotEnabled" to (glasses?.hotspot is HotspotStatus.Enabled),
      "media" to GlassesMediaStore.status(c),
      "enabled" to enabled(c), "signedIn" to signedIn(c), "running" to (sdk != null),
      "savedName" to prefs(c).getString("name", null),
      "connection" to (if (attempting) "connecting" else state?.glasses?.connection?.value ?: "disconnected"),
      "ready" to (state?.glasses?.ready == true), "bluetoothOn" to bluetoothOn(c), "permitted" to permitted(c),
      "battery" to glasses?.battery?.level, "firmwareVersion" to glasses?.firmware?.version,
      "firmwareSource" to glasses?.firmware?.source?.name, "appVersion" to glasses?.firmware?.appVersion,
      "scanning" to discovery, "devices" to devices.values.map { mapOf("id" to it.id, "name" to it.name) },
      "retryAttempt" to retries.attempt, "nextRetryAtMs" to nextRetryAt,
      "updateAvailable" to updateAvailable, "updateActive" to updateActive, "updateProgress" to updateProgress,
      "updateStatus" to updateStatus, "firmwareBusy" to firmwareBusy,
      "error" to error, "firmwareError" to firmwareError,
    )
  }
  fun diagnostics(c: Context): Map<String, Any?> = snapshot(c).filterKeys { it !in setOf("savedName", "devices", "wifi") } +
    mapOf("recording" to GlassesRecording.diagnostics(), "events" to trail.toList(), "energy" to RuntimeEnergyDiagnostics.sample(c),
      // UI status may contain SDK-provided error text. Export structured media events instead.
      "media" to GlassesMediaStore.status(c).filterKeys { it in setOf("pending", "transport") })
}
