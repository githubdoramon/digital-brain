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
    const val ACTION_WAKE_START = "digitalbrain.glassesruntime.WAKE_START"
    const val ACTION_WAKE_STOP = "digitalbrain.glassesruntime.WAKE_STOP"
    private const val CHANNEL_ID = "glasses_image_enhancement"
    private const val LEGACY_CHANNEL_ID = "glasses_poc2_capture"
    private const val NOTIFICATION_ID = 4312
    @Volatile private var active = false
    @Volatile private var startedAtMs: Long? = null
    @Volatile private var lastNativeTickAtMs: Long? = null
    @Volatile private var nativeTickCount = 0L
    @Volatile private var wakeListeningRequested = false
    @Volatile private var automaticCaptureActive = false

    fun status() = mapOf(
      "active" to active,
      "startedAtMs" to startedAtMs,
      "lastNativeTickAtMs" to lastNativeTickAtMs,
      "nativeTickCount" to nativeTickCount,
    )

    fun runtimeStatus() = mapOf(
      "active" to active,
      "wakeListeningRequested" to wakeListeningRequested,
      "automaticCaptureActive" to automaticCaptureActive,
      "startedAtMs" to startedAtMs,
    )

    fun start(context: Context, intervalMinutes: Int, scheduleCount: Int) {
      val intent = Intent(context, GlassesImageEnhancementService::class.java)
        .setAction(ACTION_START)
        .putExtra("interval_minutes", intervalMinutes)
        .putExtra("schedule_count", scheduleCount)
      ContextCompat.startForegroundService(context, intent)
    }

    fun stop(context: Context) {
      val intent = Intent(context, GlassesImageEnhancementService::class.java).setAction(ACTION_STOP)
      context.startService(intent)
    }

    fun startWakeRuntime(context: Context) {
      ContextCompat.startForegroundService(
        context,
        Intent(context, GlassesImageEnhancementService::class.java).setAction(ACTION_WAKE_START),
      )
    }

    fun stopWakeRuntime(context: Context) {
      context.startService(
        Intent(context, GlassesImageEnhancementService::class.java).setAction(ACTION_WAKE_STOP),
      )
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
    val action = intent?.action
    if (action == ACTION_WAKE_START) wakeListeningRequested = true
    if (action == ACTION_WAKE_STOP) wakeListeningRequested = false
    if (action == ACTION_START) automaticCaptureActive = true
    if (action == ACTION_STOP) {
      automaticCaptureActive = false
      handler.removeCallbacks(foregroundTick)
      nextTickUptimeMs = 0L
      if (!wakeListeningRequested) {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        return START_NOT_STICKY
      }
    }
    showForegroundNotification(
      intent?.getIntExtra("interval_minutes", 1) ?: 1,
      intent?.getIntExtra("schedule_count", 1) ?: 1,
    )
    active = true
    if (startedAtMs == null) startedAtMs = System.currentTimeMillis()
    if (action == ACTION_START) {
      tickIntervalMs = intent.getIntExtra("interval_minutes", 1)
        .coerceAtLeast(1) * 60_000L
      handler.removeCallbacks(foregroundTick)
      nextTickUptimeMs = SystemClock.uptimeMillis() + tickIntervalMs
      handler.postAtTime(foregroundTick, nextTickUptimeMs)
    }
    if (action == ACTION_WAKE_STOP && nextTickUptimeMs == 0L) {
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return START_NOT_STICKY
    }
    return START_STICKY
  }

  override fun onDestroy() {
    handler.removeCallbacks(foregroundTick)
    nextTickUptimeMs = 0L
    active = false
    startedAtMs = null
    lastNativeTickAtMs = null
    nativeTickCount = 0
    wakeListeningRequested = false
    automaticCaptureActive = false
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
    val scheduleText = if (wakeListeningRequested && automaticCaptureActive && scheduleCount == 1) {
      "Listening for Hey Brain and running automatic glasses capture"
    } else if (wakeListeningRequested && automaticCaptureActive) {
      "Listening for Hey Brain while automatic glasses work is active"
    } else if (wakeListeningRequested) {
      "Listening for Hey Brain on connected glasses"
    } else if (scheduleCount == 1) {
      "Capturing one photo every $intervalMinutes minute(s) for local analysis"
    } else {
      "Running $scheduleCount automatic capture schedules for local analysis"
    }
    val notification = NotificationCompat.Builder(this, CHANNEL_ID)
      .setSmallIcon(android.R.drawable.ic_menu_camera)
      .setContentTitle(if (wakeListeningRequested) "Digital Brain glasses runtime is active" else "Automatic glasses capture is active")
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
