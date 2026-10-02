package expo.modules.digitalbrainruntime

import android.Manifest
import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.PowerManager
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

internal data class GlassesAlertConfig(
  val notifications: Boolean, val calls: Boolean, val packages: Set<String>,
  val chimeVolume: Int, val callVolume: Int,
)

internal object GlassesAlertSettings {
  private const val PREFS = "digital_brain_glasses_alerts_v2"
  fun config(c: Context): GlassesAlertConfig {
    val p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    return GlassesAlertConfig(p.getBoolean("notifications", false), p.getBoolean("calls", false),
      p.getStringSet("packages", emptySet())?.toSet() ?: emptySet(),
      p.getInt("chime_volume", 25).coerceIn(0, 100), p.getInt("call_volume", 35).coerceIn(0, 100))
  }
  fun save(c: Context, notifications: Boolean, calls: Boolean, packages: List<String>, chime: Int, call: Int) {
    require(chime in 0..100 && call in 0..100) { "Volume must be between 0 and 100" }
    require(packages.size <= 160 && packages.all { it.matches(Regex("[A-Za-z0-9_.]{3,255}")) }) { "Invalid app selection" }
    check(c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
      .putBoolean("notifications", notifications).putBoolean("calls", calls)
      .putStringSet("packages", packages.toSet()).putInt("chime_volume", chime)
      .putInt("call_volume", call).commit()) { "Could not save glasses alerts" }
    GlassesAlertPlayback.stop()
    GlassesAlertNotificationListenerService.refreshSettings()
  }
  fun access(c: Context) = NotificationManagerCompat.getEnabledListenerPackages(c).contains(c.packageName)
  fun phonePermission(c: Context) = ContextCompat.checkSelfPermission(c, Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED
  fun phoneInUse(c: Context): Boolean = c.getSystemService(PowerManager::class.java).isInteractive &&
    !c.getSystemService(KeyguardManager::class.java).isKeyguardLocked
  fun eligible(c: Context) = RuntimeGlasses.signedIn(c) && RuntimeGlasses.enabled(c)
  fun audioDevice(c: Context): AudioDeviceInfo? {
    if (!RuntimeGlasses.alertsReady()) return null
    val names = RuntimeGlasses.alertAudioNames(c).map { it.lowercase() }.toSet()
    if (names.isEmpty()) return null
    return c.getSystemService(AudioManager::class.java).getDevices(AudioManager.GET_DEVICES_OUTPUTS)
      .firstOrNull { it.type in setOf(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET,
        AudioDeviceInfo.TYPE_BLE_SPEAKER) && it.productName.toString().trim().lowercase() in names }
  }
  fun dndBlocksMedia(c: Context): Boolean {
    val manager = c.getSystemService(NotificationManager::class.java)
    return when (manager.currentInterruptionFilter) {
      NotificationManager.INTERRUPTION_FILTER_NONE, NotificationManager.INTERRUPTION_FILTER_ALARMS -> true
      NotificationManager.INTERRUPTION_FILTER_PRIORITY -> {
        if (Build.VERSION.SDK_INT >= 30) {
          try { manager.consolidatedNotificationPolicy.priorityCategories and NotificationManager.Policy.PRIORITY_CATEGORY_MEDIA == 0 }
          catch (_: SecurityException) { false } // Android still enforces its policy on the audio track.
        } else false
      }
      else -> false
    }
  }
  fun apps(c: Context): List<Map<String, String>> = c.packageManager
    .queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
    .map { it.activityInfo.applicationInfo }.distinctBy { it.packageName }
    .map { mapOf("packageName" to it.packageName, "label" to c.packageManager.getApplicationLabel(it).toString()) }
    .sortedBy { it["label"]?.lowercase() }
  fun status(c: Context): Map<String, Any?> {
    val config = config(c)
    val manager = c.getSystemService(AudioManager::class.java)
    val filter = c.getSystemService(NotificationManager::class.java).currentInterruptionFilter
    return mapOf("notifications" to config.notifications, "calls" to config.calls,
      "packages" to config.packages.sorted(), "chimeVolume" to config.chimeVolume, "callVolume" to config.callVolume,
      "notificationAccess" to access(c), "listenerConnected" to GlassesAlertNotificationListenerService.connected(),
      "phonePermission" to phonePermission(c), "glassesReady" to RuntimeGlasses.alertsReady(),
      "audioReady" to (audioDevice(c) != null), "phoneInUse" to phoneInUse(c),
      "mediaMuted" to (manager.isStreamMute(AudioManager.STREAM_MUSIC) || manager.getStreamVolume(AudioManager.STREAM_MUSIC) == 0),
      "dndActive" to (filter != NotificationManager.INTERRUPTION_FILTER_ALL && filter != NotificationManager.INTERRUPTION_FILTER_UNKNOWN),
      "dndBlocksMedia" to dndBlocksMedia(c), "playing" to GlassesAlertPlayback.playing(), "lastPlaybackError" to GlassesAlertPlayback.lastError)
  }
  fun openAccess(c: Context) { c.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
  fun openSound(c: Context) { c.startActivity(Intent(Settings.ACTION_SOUND_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}
