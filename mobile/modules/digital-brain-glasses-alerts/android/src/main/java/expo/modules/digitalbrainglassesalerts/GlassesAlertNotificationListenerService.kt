package expo.modules.digitalbrainglassesalerts

import android.app.Notification
import android.content.Intent
import android.util.Log
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.telephony.PhoneStateListener
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat

/**
 * Android's notification-access service is the only cross-app notification
 * boundary. It filters by package locally and never reads, sends, or persists
 * notification titles, text, people, or call numbers.
 */
class GlassesAlertNotificationListenerService : NotificationListenerService() {
  companion object {
    private const val TAG = "GlassesAlerts"
    @Volatile private var activeInstance: GlassesAlertNotificationListenerService? = null

    fun refreshPhoneStateListener() {
      activeInstance?.registerPhoneStateListener()
    }
  }

  private var telephonyManager: TelephonyManager? = null

  private val phoneStateListener = object : PhoneStateListener() {
    override fun onCallStateChanged(state: Int, phoneNumber: String?) {
      when (state) {
        TelephonyManager.CALL_STATE_RINGING -> startCallAlert()
        TelephonyManager.CALL_STATE_IDLE,
        TelephonyManager.CALL_STATE_OFFHOOK,
        -> stopCallAlert()
      }
    }
  }

  override fun onListenerConnected() {
    super.onListenerConnected()
    activeInstance = this
    registerPhoneStateListener()
  }

  override fun onListenerDisconnected() {
    unregisterPhoneStateListener()
    stopCallAlert()
    if (activeInstance === this) activeInstance = null
    super.onListenerDisconnected()
  }

  override fun onNotificationPosted(notification: StatusBarNotification) {
    val config = GlassesAlertSettings.config(this)
    if (
      !config.enabled ||
      notification.packageName == packageName ||
      notification.notification.category == Notification.CATEGORY_CALL
    ) return
    if (notification.packageName in config.selectedPackages) {
      GlassesAlertPlayback.playNotificationAlert(this)
    }
  }

  private fun registerPhoneStateListener() {
    if (!GlassesAlertSettings.isPhoneStatePermissionGranted(this)) return
    val manager = getSystemService(TelephonyManager::class.java) ?: return
    if (telephonyManager === manager) return
    unregisterPhoneStateListener()
    telephonyManager = manager
    @Suppress("DEPRECATION")
    manager.listen(phoneStateListener, PhoneStateListener.LISTEN_CALL_STATE)
  }

  private fun unregisterPhoneStateListener() {
    @Suppress("DEPRECATION")
    telephonyManager?.listen(phoneStateListener, PhoneStateListener.LISTEN_NONE)
    telephonyManager = null
  }

  private fun startCallAlert() {
    if (!GlassesAlertSettings.config(this).enabled) return
    if (!GlassesAlertPlayback.startCallAlert(this)) {
      Log.d(TAG, "Incoming call alert was suppressed or has no glasses audio route.")
      return
    }
    val intent = Intent(this, GlassesAlertPlaybackService::class.java)
      .setAction(GlassesAlertPlaybackService.ACTION_START_CALL_ALERT)
    try {
      ContextCompat.startForegroundService(this, intent)
    } catch (error: IllegalStateException) {
      // The notification-listener service already owns the active repeating
      // loop. Some Android/OEM policies reject a background FGS transition;
      // keep that loop running rather than degrading to a one-shot preview.
      Log.w(TAG, "Could not promote the active call alert to a foreground service.", error)
    }
  }

  private fun stopCallAlert() {
    stopService(Intent(this, GlassesAlertPlaybackService::class.java)
      .setAction(GlassesAlertPlaybackService.ACTION_STOP_CALL_ALERT))
    GlassesAlertPlayback.stopCallAlert()
  }
}
