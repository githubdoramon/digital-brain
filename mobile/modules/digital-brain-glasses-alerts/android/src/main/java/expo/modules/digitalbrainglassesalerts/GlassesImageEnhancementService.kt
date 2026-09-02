package expo.modules.digitalbrainglassesalerts

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import androidx.core.app.NotificationCompat

/**
 * Keeps the app process eligible for the image enhancement scheduler while the
 * app is backgrounded. Camera requests remain owned by the JS coordinator;
 * this service only provides the Android foreground-service lifetime and a
 * persistent user-visible status notification.
 */
class GlassesImageEnhancementService : Service() {
  companion object {
    const val ACTION_START = "digitalbrain.imageenhancement.START"
    const val ACTION_STOP = "digitalbrain.imageenhancement.STOP"
    private const val CHANNEL_ID = "glasses_image_enhancement"
    private const val LEGACY_CHANNEL_ID = "glasses_poc2_capture"
    private const val NOTIFICATION_ID = 4312
    @Volatile private var active = false
    @Volatile private var startedAtMs: Long? = null
    @Volatile private var lastNativeTickAtMs: Long? = null
    @Volatile private var nativeTickCount = 0L

    fun status() = mapOf(
      "active" to active,
      "startedAtMs" to startedAtMs,
      "lastNativeTickAtMs" to lastNativeTickAtMs,
      "nativeTickCount" to nativeTickCount,
    )

    fun start(context: Context, intervalMinutes: Int, scheduleCount: Int) {
      val intent = Intent(context, GlassesImageEnhancementService::class.java)
        .setAction(ACTION_START)
        .putExtra("interval_minutes", intervalMinutes)
        .putExtra("schedule_count", scheduleCount)
      ContextCompat.startForegroundService(context, intent)
    }

    fun stop(context: Context) {
      context.stopService(Intent(context, GlassesImageEnhancementService::class.java))
    }
  }

  private val handler = Handler(Looper.getMainLooper())
  private var tickIntervalMs = 60_000L
  private var nextTickUptimeMs = 0L
  private val foregroundTick = object : Runnable {
    override fun run() {
      lastNativeTickAtMs = System.currentTimeMillis()
      nativeTickCount += 1
      GlassesAlertsModule.emitImageEnhancementForegroundTick()
      // Keep ticks aligned to the configured cadence. `postDelayed` from the
      // end of a late main-thread callback accumulates drift, which made a
      // one-minute setting turn into 80–140-second camera intervals.
      val now = SystemClock.uptimeMillis()
      do {
        nextTickUptimeMs += tickIntervalMs
      } while (nextTickUptimeMs <= now)
      handler.postAtTime(this, nextTickUptimeMs)
    }
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    if (intent?.action == ACTION_STOP) {
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return START_NOT_STICKY
    }
    showForegroundNotification(
      intent?.getIntExtra("interval_minutes", 1) ?: 1,
      intent?.getIntExtra("schedule_count", 1) ?: 1,
    )
    active = true
    if (startedAtMs == null) startedAtMs = System.currentTimeMillis()
    tickIntervalMs = (intent?.getIntExtra("interval_minutes", 1) ?: 1)
      .coerceAtLeast(1) * 60_000L
    handler.removeCallbacks(foregroundTick)
    nextTickUptimeMs = SystemClock.uptimeMillis() + tickIntervalMs
    handler.postAtTime(foregroundTick, nextTickUptimeMs)
    return START_STICKY
  }

  override fun onDestroy() {
    handler.removeCallbacks(foregroundTick)
    nextTickUptimeMs = 0L
    active = false
    startedAtMs = null
    lastNativeTickAtMs = null
    nativeTickCount = 0
    super.onDestroy()
  }

  override fun onBind(intent: Intent?): IBinder? = null

  private fun showForegroundNotification(intervalMinutes: Int, scheduleCount: Int) {
    val manager = getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      manager.deleteNotificationChannel(LEGACY_CHANNEL_ID)
      manager.createNotificationChannel(
        NotificationChannel(
          CHANNEL_ID,
          "Automatic glasses capture",
          NotificationManager.IMPORTANCE_LOW,
        ).apply { setSound(null, null) },
      )
    }
    val scheduleText = if (scheduleCount == 1) {
      "Capturing one photo every $intervalMinutes minute(s) for local analysis"
    } else {
      "Running $scheduleCount automatic capture schedules for local analysis"
    }
    val notification = NotificationCompat.Builder(this, CHANNEL_ID)
      .setSmallIcon(android.R.drawable.ic_menu_camera)
      .setContentTitle("Automatic glasses capture is active")
      .setContentText(scheduleText)
      .setOngoing(true)
      .setSilent(true)
      .build()
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      startForeground(
        NOTIFICATION_ID,
        notification,
        ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
      )
    } else {
      startForeground(NOTIFICATION_ID, notification)
    }
  }
}
