package expo.modules.digitalbrainruntime

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
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

/** The app's native foreground service for background location capture. */
class DigitalBrainRuntimeService : Service() {
  private lateinit var location: RuntimeLocationCapture
  private var notificationOwners: Set<RuntimeFeature>? = null
  var foregroundTypes = 0
    private set
  val locationActive get() = location.active
  var startedAtMs = System.currentTimeMillis()
    private set

  override fun onCreate() {
    super.onCreate()
    location = RuntimeLocationCapture(this) { RuntimeLocationUploadScheduler.enqueue(this, "location_batch") }
    DigitalBrainRuntime.service = this
    RuntimeLocationDiagnostics.record(this, "runtime_service_created")
    Log.i("DigitalBrainRuntime", "service_created")
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    RuntimeLocationDiagnostics.record(this, "runtime_service_start_command", mapOf(
      "attempt" to startId,
      "owner_enabled" to (RuntimeFeature.LOCATION in DigitalBrainRuntime.owners(this)),
    ))
    if (intent?.action == "stop_glasses_recording") GlassesRecording.stop()
    refresh(forceNotification = true)
    if (foregroundTypes == 0) return START_NOT_STICKY
    RuntimeLocationUploadScheduler.enqueueIfPending(this, "service_started")
    return START_STICKY
  }

  private fun granted(permission: String) =
    ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

  fun refresh(forceNotification: Boolean = false) {
    val owners = DigitalBrainRuntime.owners(this)
    val wantsLocation = RuntimeFeature.LOCATION in owners
    val locationPermitted =
      (granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION)) &&
      getSystemService(LocationManager::class.java).isLocationEnabled &&
      (Build.VERSION.SDK_INT < 29 || DigitalBrainRuntime.activityVisible ||
        foregroundTypes and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0 ||
        granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION))
    RuntimeLocationDiagnostics.record(this, "runtime_location_eligibility_checked", mapOf(
      "wants_location" to wantsLocation,
      "fine_permission" to granted(Manifest.permission.ACCESS_FINE_LOCATION),
      "coarse_permission" to granted(Manifest.permission.ACCESS_COARSE_LOCATION),
      "background_permission" to granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION),
      "location_services_enabled" to getSystemService(LocationManager::class.java).isLocationEnabled,
      "foreground_types" to foregroundTypes,
    ))
    val locationTypes = if (wantsLocation && locationPermitted) {
      android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
    } else 0
    val wantsGlasses = RuntimeFeature.GLASSES in owners && RuntimeGlasses.signedIn(this)
    val glassesTypes = if (wantsGlasses && RuntimeGlasses.permitted(this)) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0
    val recordingTypes = if (GlassesRecording.capturing && glassesTypes != 0 && granted(Manifest.permission.RECORD_AUDIO))
      ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
    val types = locationTypes or glassesTypes or recordingTypes
    if (wantsLocation && !locationPermitted) {
      DigitalBrainRuntime.lastError = "Location permission or device location is unavailable"
      RuntimeLocationDiagnostics.record(this, "runtime_location_blocked", mapOf(
        "reason" to "permission_or_location_services_unavailable",
      ))
    }
    if (types == 0) {
      RuntimeGlasses.stop()
      location.stop("runtime_not_permitted_or_not_owned")
      foregroundTypes = 0
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return
    }
    try {
      if (forceNotification || types != foregroundTypes || owners != notificationOwners) showNotification(types)
    } catch (error: SecurityException) {
      location.stop("foreground_promotion_rejected")
      foregroundTypes = 0
      DigitalBrainRuntime.lastError = "Foreground location promotion rejected: ${error.javaClass.simpleName}"
      RuntimeLocationDiagnostics.record(this, "foreground_promotion_rejected", mapOf(
        "error_type" to error.javaClass.simpleName,
      ))
      Log.w("DigitalBrainRuntime", "foreground_promotion_rejected", error)
      stopSelf()
      return
    }
    foregroundTypes = types
    if (glassesTypes != 0) {
      try { RuntimeGlasses.start(this) } catch (error: RuntimeException) {
        RuntimeGlasses.stop()
        DigitalBrainRuntime.lastError = "Glasses start failed: ${error.javaClass.simpleName}"
      }
    } else RuntimeGlasses.stop()
    notificationOwners = owners
    if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION != 0) {
      try { location.start() } catch (error: RuntimeException) {
        DigitalBrainRuntime.lastError = "Location start failed: ${error.javaClass.simpleName}"
        RuntimeLocationDiagnostics.record(this, "capture_start_failed", mapOf(
          "error_type" to error.javaClass.simpleName,
        ))
        Log.w("DigitalBrainRuntime", "location_start_failed", error)
      }
      } else location.stop("location_not_active")
  }

  private fun showNotification(types: Int) {
    val manager = getSystemService(NotificationManager::class.java)
    manager.createNotificationChannel(NotificationChannel("digital_brain_runtime", "Digital Brain activity", NotificationManager.IMPORTANCE_LOW).apply { setSound(null, null) })
    val body = if (GlassesRecording.capturing) "Glasses microphone is active. Tap Stop recording to save."
      else when (types) {
        ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE -> "Maintaining your glasses connection"
        ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION -> "Background location capture is active"
        else -> "Glasses connection and location tracking are active"
      }
    val builder = NotificationCompat.Builder(this, "digital_brain_runtime")
      .setSmallIcon(android.R.drawable.ic_dialog_info)
      .setContentTitle(if (GlassesRecording.capturing) "Recording from glasses" else "Digital Brain activity")
      .setContentText(body)
      .setStyle(NotificationCompat.BigTextStyle().bigText(body))
      .setPriority(NotificationCompat.PRIORITY_LOW).setOngoing(true).setSilent(true).setOnlyAlertOnce(true)
    if (GlassesRecording.capturing) {
      val stop = Intent(this, DigitalBrainRuntimeService::class.java).setAction("stop_glasses_recording")
      builder.addAction(android.R.drawable.ic_media_pause, "Stop recording",
        PendingIntent.getService(this, 1, stop, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
    }
    packageManager.getLaunchIntentForPackage(packageName)?.let {
      builder.setContentIntent(PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
    }
    if (Build.VERSION.SDK_INT >= 29) startForeground(4312, builder.build(), types)
    else startForeground(4312, builder.build())
    // Remove obsolete notification rows left by an upgrade, after our replacement exists.
    listOf(1001, 4017, 4308).forEach(manager::cancel)
  }

  override fun onDestroy() {
    // Recording teardown must not promote a service that is being destroyed.
    if (DigitalBrainRuntime.service === this) DigitalBrainRuntime.service = null
    RuntimeGlasses.stop()
    location.stop("service_destroyed")
    foregroundTypes = 0
    RuntimeLocationDiagnostics.record(this, "runtime_service_destroyed")
    Log.i("DigitalBrainRuntime", "service_destroyed")
    super.onDestroy()
  }
  override fun onBind(intent: Intent?): IBinder? = null
}
