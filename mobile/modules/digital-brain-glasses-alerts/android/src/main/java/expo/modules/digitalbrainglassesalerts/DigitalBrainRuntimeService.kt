package expo.modules.digitalbrainglassesalerts

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

/** The only foreground service for the app's glasses and location runtime. */
class DigitalBrainRuntimeService : Service() {
  private val handler = Handler(Looper.getMainLooper())
  private lateinit var location: RuntimeLocationCapture
  private val workCadence = RuntimeWorkCadence()
  private var notificationOwners: Set<RuntimeFeature>? = null
  var foregroundTypes = 0
    private set
  val locationActive get() = location.active
  var startedAtMs = System.currentTimeMillis()
    private set
  var lastTickAtMs: Long? = null
    private set
  var tickCount = 0L
    private set
  private val tick = object : Runnable {
    override fun run() {
      lastTickAtMs = System.currentTimeMillis()
      tickCount++
      refresh(rescheduleTick = false)
      if (foregroundTypes == 0) return
      val owners = DigitalBrainRuntime.owners(this@DigitalBrainRuntimeService)
      if (RuntimeFeature.CAPTURE in owners) {
        GlassesAlertsModule.emitImageEnhancementForegroundTick()
      }
      if (workCadence.shouldRequest(SystemClock.elapsedRealtime(), owners)) requestWork("periodic")
      handler.postDelayed(this, if (RuntimeFeature.CAPTURE in owners) 60_000L else 300_000L)
    }
  }

  override fun onCreate() {
    super.onCreate()
    location = RuntimeLocationCapture(this) { handler.post { requestWork("location_batch") } }
    DigitalBrainRuntime.service = this
    Log.i("DigitalBrainRuntime", "service_created")
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    refresh(forceNotification = true, rescheduleTick = false)
    if (foregroundTypes == 0) return START_NOT_STICKY
    handler.removeCallbacks(tick)
    handler.post(tick)
    return START_STICKY
  }

  private fun granted(permission: String) =
    ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

  fun refresh(forceNotification: Boolean = false, rescheduleTick: Boolean = true) {
    val owners = DigitalBrainRuntime.owners(this)
    val captureChanged = (RuntimeFeature.CAPTURE in owners) != (notificationOwners?.contains(RuntimeFeature.CAPTURE) == true)
    val wantsLocation = RuntimeFeature.LOCATION in owners
    val locationPermitted =
      (granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION)) &&
      getSystemService(LocationManager::class.java).isLocationEnabled &&
      (Build.VERSION.SDK_INT < 29 || DigitalBrainRuntime.activityVisible ||
        foregroundTypes and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0 ||
        granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION))
    val glasses = owners.any { it in setOf(RuntimeFeature.GLASSES, RuntimeFeature.CAPTURE, RuntimeFeature.WAKE, RuntimeFeature.RECORDING) }
    var types = 0
    if (wantsLocation && locationPermitted) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
    if (glasses) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
    if (RuntimeFeature.CALL in owners) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
    if (wantsLocation && !locationPermitted) DigitalBrainRuntime.lastError = "Location permission or device location is unavailable"
    if (types == 0) {
      location.stop()
      foregroundTypes = 0
      handler.removeCallbacks(tick)
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return
    }
    try {
      if (forceNotification || types != foregroundTypes || owners != notificationOwners) showNotification(types, owners)
    } catch (error: SecurityException) {
      // A permission dialog/activity transition can race promotion. Keep other owners
      // alive, but never claim location capture succeeded after the OS rejected it.
      types = types and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION.inv()
      DigitalBrainRuntime.lastError = "Foreground type promotion rejected: ${error.javaClass.simpleName}"
      Log.w("DigitalBrainRuntime", "foreground_promotion_rejected", error)
      if (types == 0) {
        location.stop()
        foregroundTypes = 0
        stopSelf()
        return
      }
      showNotification(types, owners)
    }
    foregroundTypes = types
    notificationOwners = owners
    if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0) {
      try { location.start() } catch (error: RuntimeException) {
        DigitalBrainRuntime.lastError = "Location start failed: ${error.javaClass.simpleName}"
        Log.w("DigitalBrainRuntime", "location_start_failed", error)
      }
    } else location.stop()
    if (captureChanged && rescheduleTick) {
      handler.removeCallbacks(tick)
      handler.post(tick)
    }
  }

  private fun showNotification(types: Int, owners: Set<RuntimeFeature>) {
    val manager = getSystemService(NotificationManager::class.java)
    manager.createNotificationChannel(NotificationChannel("digital_brain_runtime", "Digital Brain activity", NotificationManager.IMPORTANCE_LOW).apply { setSound(null, null) })
    val descriptions = mutableListOf<String>()
    if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0) descriptions.add("Location tracking")
    if (RuntimeFeature.RECORDING in owners) descriptions.add("Recording glasses audio")
    if (RuntimeFeature.WAKE in owners) descriptions.add("Hey Brain listening")
    if (RuntimeFeature.CAPTURE in owners) descriptions.add("Automatic glasses capture")
    if (RuntimeFeature.CALL in owners) descriptions.add("Incoming call alert")
    if (RuntimeFeature.GLASSES in owners && descriptions.none { it.contains("glasses", true) }) descriptions.add("Glasses")
    val builder = NotificationCompat.Builder(this, "digital_brain_runtime")
      .setSmallIcon(android.R.drawable.ic_dialog_info)
      .setContentTitle("Digital Brain is active")
      .setContentText(descriptions.joinToString(" · "))
      .setStyle(NotificationCompat.BigTextStyle().bigText(descriptions.joinToString(" · ")))
      .setPriority(NotificationCompat.PRIORITY_LOW).setOngoing(true).setSilent(true).setOnlyAlertOnce(true)
    packageManager.getLaunchIntentForPackage(packageName)?.let {
      builder.setContentIntent(PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
    }
    if (Build.VERSION.SDK_INT >= 29) startForeground(4312, builder.build(), types)
    else startForeground(4312, builder.build())
    // Remove obsolete notification rows left by an upgrade, after our replacement exists.
    listOf(1001, 4017, 4308).forEach(manager::cancel)
  }

  private fun requestWork(reason: String) {
    if (foregroundTypes != 0) {
      workCadence.requested(SystemClock.elapsedRealtime())
      RuntimeWorkService.request(this, reason)
    }
  }

  override fun onDestroy() {
    handler.removeCallbacks(tick)
    location.stop()
    foregroundTypes = 0
    if (DigitalBrainRuntime.service === this) DigitalBrainRuntime.service = null
    Log.i("DigitalBrainRuntime", "service_destroyed")
    super.onDestroy()
  }
  override fun onBind(intent: Intent?): IBinder? = null
}
