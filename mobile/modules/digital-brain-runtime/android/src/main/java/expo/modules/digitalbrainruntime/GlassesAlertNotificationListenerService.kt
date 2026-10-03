package expo.modules.digitalbrainruntime

import android.app.Notification
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.telecom.TelecomManager
import android.telephony.PhoneStateListener
import android.telephony.TelephonyManager
import android.telephony.TelephonyCallback
import androidx.core.content.ContextCompat

/** Reads package, flags and call lifecycle metadata only. No titles, text, contacts or numbers. */
class GlassesAlertNotificationListenerService : NotificationListenerService() {
  companion object {
    private var instance: GlassesAlertNotificationListenerService? = null
    private val main = Handler(Looper.getMainLooper())
    fun connected() = instance != null
    fun refreshSettings() { main.post { instance?.refresh() } }
    fun connectionChanged() { main.post { instance?.reconcile() } }
  }
  private val handler = Handler(Looper.getMainLooper())
  private val calls = GlassesCallSources()
  private val seen = linkedMapOf<String, Long>()
  private var expiry: Runnable? = null
  private var telephony: TelephonyManager? = null
  private var phoneRinging = false
  private val dialerSources = linkedSetOf<String>()
  private var modernPhone: TelephonyCallback? = null
  @Suppress("DEPRECATION")
  private val phone = object : PhoneStateListener() {
    override fun onCallStateChanged(state: Int, ignoredNumber: String?) { onPhoneState(state) }
  }
  private fun onPhoneState(state: Int) {
    if (telephony == null || !GlassesAlertSettings.phonePermission(this)) {
      calls.ended("cellular"); reconcile(); return
    }
    if (state == TelephonyManager.CALL_STATE_RINGING && enabledCalls()) {
      phoneRinging = true
      calls.incoming("cellular", SystemClock.elapsedRealtime())
    } else {
      calls.ended("cellular")
      if (phoneRinging) {
        // Clear the dialer's duplicate when telephony reports this ringing call ended/answered.
        // An initial idle callback must not cancel another SIM's incoming notification.
        dialerSources.forEach(calls::ended)
        dialerSources.clear()
      }
      phoneRinging = false
    }
    reconcile()
  }
  override fun onListenerConnected() {
    super.onListenerConnected()
    instance = this
    try { activeNotifications?.takeLast(256)?.forEach { seen[it.key] = notificationTimestamp(it) } } catch (_: SecurityException) { }
    refresh()
  }
  override fun onListenerDisconnected() {
    shutdown()
    super.onListenerDisconnected()
  }
  override fun onDestroy() { shutdown(); super.onDestroy() }

  private fun enabledCalls() = GlassesAlertSettings.eligible(this) && GlassesAlertSettings.config(this).calls
  private fun refresh() {
    if (!enabledCalls() || !GlassesAlertSettings.phonePermission(this)) unregisterPhone()
    else if (telephony == null) {
      try {
        val manager = getSystemService(TelephonyManager::class.java)
        telephony = manager
        if (Build.VERSION.SDK_INT >= 31) {
          val callback = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
            override fun onCallStateChanged(state: Int) { onPhoneState(state) }
          }
          modernPhone = callback
          manager.registerTelephonyCallback(ContextCompat.getMainExecutor(this), callback)
        } else {
          @Suppress("DEPRECATION")
          manager.listen(phone, PhoneStateListener.LISTEN_CALL_STATE)
        }
      } catch (_: RuntimeException) { unregisterPhone() }
    }
    if (!enabledCalls()) { calls.clear(); dialerSources.clear() }
    // Resume only currently incoming calls; never replay the existing notification inbox.
    if (enabledCalls()) {
      try { activeNotifications?.forEach { processCall(it) } } catch (_: SecurityException) { }
    }
    reconcile()
  }
  private fun unregisterPhone() {
    try {
      if (Build.VERSION.SDK_INT >= 31) modernPhone?.let { telephony?.unregisterTelephonyCallback(it) }
      else {
        @Suppress("DEPRECATION")
        telephony?.listen(phone, PhoneStateListener.LISTEN_NONE)
      }
    } catch (_: RuntimeException) { }
    modernPhone = null
    telephony = null
    phoneRinging = false
    calls.ended("cellular")
  }
  private fun shutdown() {
    unregisterPhone(); calls.clear(); dialerSources.clear(); seen.clear()
    expiry?.let(handler::removeCallbacks); expiry = null
    GlassesAlertPlayback.stop()
    if (instance === this) instance = null
  }

  private fun kind(notification: Notification): GlassesCallKind {
    val type = try { notification.extras?.getInt("android.callType", 0) ?: 0 } catch (_: RuntimeException) { 0 }
    return GlassesAlertPolicy.callKind(notification.category == Notification.CATEGORY_CALL, type, notification.fullScreenIntent != null)
  }
  private fun processCall(post: StatusBarNotification): GlassesCallKind {
    val value = kind(post.notification)
    val callType = post.notification.extras?.getInt("android.callType", 0) ?: 0
    if (GlassesRecordingPolicy.callInterrupts(value == GlassesCallKind.INCOMING, callType,
      post.notification.category == Notification.CATEGORY_CALL,
      post.notification.flags and Notification.FLAG_ONGOING_EVENT != 0)) {
      GlassesRecording.stop(GlassesRecordingStop.CALL)
    }
    val dialer = getSystemService(TelecomManager::class.java)?.defaultDialerPackage
    val cellularNotification = post.packageName == dialer || post.packageName == "com.android.server.telecom"
    if (value == GlassesCallKind.INCOMING && enabledCalls()) {
      calls.incoming(post.key, SystemClock.elapsedRealtime())
      if (cellularNotification && dialerSources.size < 32) dialerSources.add(post.key)
    } else {
      calls.ended(post.key)
      dialerSources.remove(post.key)
    }
    return value
  }
  private fun notificationTimestamp(post: StatusBarNotification) = post.notification.`when`.takeIf { it > 0 } ?: post.postTime
  override fun onNotificationPosted(post: StatusBarNotification) {
    val value = processCall(post)
    reconcile()
    val timestamp = notificationTimestamp(post)
    val fresh = GlassesAlertPolicy.isNewPost(seen.put(post.key, timestamp), timestamp,
      post.notification.flags and Notification.FLAG_ONLY_ALERT_ONCE != 0)
    if (seen.size > 256) seen.remove(seen.keys.first())
    if (value != GlassesCallKind.NONE || !GlassesAlertSettings.eligible(this)) return
    val config = GlassesAlertSettings.config(this)
    if (!config.notifications) return
    val flags = post.notification.flags
    if (GlassesAlertPolicy.shouldChime(post.packageName in config.packages, fresh,
        flags and (Notification.FLAG_ONGOING_EVENT or Notification.FLAG_FOREGROUND_SERVICE) != 0,
        flags and Notification.FLAG_GROUP_SUMMARY != 0,
        GlassesAlertSettings.phoneInUse(this), calls.active(), SystemClock.elapsedRealtime(), null)) {
      GlassesAlertPlayback.chime(this)
    }
  }
  override fun onNotificationRemoved(post: StatusBarNotification) {
    seen.remove(post.key)
    dialerSources.remove(post.key)
    calls.ended(post.key)
    reconcile()
  }
  private fun reconcile() {
    expiry?.let(handler::removeCallbacks); expiry = null
    if (!enabledCalls()) { calls.clear(); dialerSources.clear() }
    calls.expire(SystemClock.elapsedRealtime())
    if (calls.active() && RuntimeGlasses.alertsReady()) GlassesAlertPlayback.call(this)
    else GlassesAlertPlayback.stopCall()
    calls.nextExpiry()?.let { deadline ->
      expiry = Runnable { reconcile() }.also { handler.postDelayed(it, (deadline - SystemClock.elapsedRealtime()).coerceAtLeast(1)) }
    }
  }
}
