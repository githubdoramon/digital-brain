package expo.modules.digitalbrainglassesalerts

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/** Keeps a selected-glasses call ring alive while the phone is ringing. */
class GlassesAlertPlaybackService : Service() {
  companion object {
    const val ACTION_START_CALL_ALERT = "digitalbrain.glassesalerts.START_CALL_ALERT"
    const val ACTION_STOP_CALL_ALERT = "digitalbrain.glassesalerts.STOP_CALL_ALERT"
    private const val CHANNEL_ID = "glasses_alert_call"
    private const val NOTIFICATION_ID = 4308
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    when (intent?.action) {
      ACTION_START_CALL_ALERT -> {
        showForegroundNotification()
        if (!GlassesAlertPlayback.isCallAlertActive() && !GlassesAlertPlayback.startCallAlert(this)) {
          stopForeground(STOP_FOREGROUND_REMOVE)
          stopSelf()
        }
      }
      ACTION_STOP_CALL_ALERT -> {
        GlassesAlertPlayback.stopCallAlert()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
      }
    }
    return START_NOT_STICKY
  }

  override fun onDestroy() {
    GlassesAlertPlayback.stopCallAlert()
    super.onDestroy()
  }

  override fun onBind(intent: Intent?): IBinder? = null

  private fun showForegroundNotification() {
    val manager = getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      manager.createNotificationChannel(
        NotificationChannel(CHANNEL_ID, "Smart glasses call alert", NotificationManager.IMPORTANCE_MIN)
          .apply { setSound(null, null) },
      )
    }
    val notification = NotificationCompat.Builder(this, CHANNEL_ID)
      .setSmallIcon(android.R.drawable.stat_sys_phone_call)
      .setContentTitle("Incoming call alerting your glasses")
      .setSilent(true)
      .setOngoing(true)
      .build()
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
    } else {
      startForeground(NOTIFICATION_ID, notification)
    }
  }
}
