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
    val types = if (wantsLocation && locationPermitted) {
      android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
    } else 0
    if (wantsLocation && !locationPermitted) {
      DigitalBrainRuntime.lastError = "Location permission or device location is unavailable"
      RuntimeLocationDiagnostics.record(this, "runtime_location_blocked", mapOf(
        "reason" to "permission_or_location_services_unavailable",
      ))
    }
    if (types == 0) {
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
    val builder = NotificationCompat.Builder(this, "digital_brain_runtime")
      .setSmallIcon(android.R.drawable.ic_dialog_info)
      .setContentTitle("Digital Brain location tracking")
      .setContentText("Background location capture is active")
      .setStyle(NotificationCompat.BigTextStyle().bigText("Background location capture is active"))
      .setPriority(NotificationCompat.PRIORITY_LOW).setOngoing(true).setSilent(true).setOnlyAlertOnce(true)
    packageManager.getLaunchIntentForPackage(packageName)?.let {
      builder.setContentIntent(PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
    }
    if (Build.VERSION.SDK_INT >= 29) startForeground(4312, builder.build(), types)
    else startForeground(4312, builder.build())
    // Remove obsolete notification rows left by an upgrade, after our replacement exists.
    listOf(1001, 4017, 4308).forEach(manager::cancel)
  }

  override fun onDestroy() {
    location.stop("service_destroyed")
    foregroundTypes = 0
    if (DigitalBrainRuntime.service === this) DigitalBrainRuntime.service = null
    RuntimeLocationDiagnostics.record(this, "runtime_service_destroyed")
    Log.i("DigitalBrainRuntime", "service_destroyed")
    super.onDestroy()
  }
  override fun onBind(intent: Intent?): IBinder? = null
}
