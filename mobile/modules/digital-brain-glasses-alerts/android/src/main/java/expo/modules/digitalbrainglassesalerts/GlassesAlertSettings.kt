package expo.modules.digitalbrainglassesalerts

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.app.KeyguardManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat

internal data class GlassesAlertConfig(
  val enabled: Boolean,
  val selectedPackages: Set<String>,
  val expectedAudioDeviceName: String?,
)

internal object GlassesAlertSettings {
  private const val PREFERENCES = "digital_brain_glasses_alerts"
  private const val ENABLED = "enabled"
  private const val SELECTED_PACKAGES = "selected_packages"
  private const val EXPECTED_AUDIO_DEVICE_NAME = "expected_audio_device_name"

  fun config(context: Context): GlassesAlertConfig {
    val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    return GlassesAlertConfig(
      enabled = preferences.getBoolean(ENABLED, false),
      selectedPackages = preferences.getStringSet(SELECTED_PACKAGES, emptySet()) ?: emptySet(),
      expectedAudioDeviceName = preferences.getString(EXPECTED_AUDIO_DEVICE_NAME, null)
        ?.trim()
        ?.takeIf { it.isNotEmpty() },
    )
  }

  fun save(context: Context, enabled: Boolean, selectedPackages: Collection<String>): GlassesAlertConfig {
    val packages = selectedPackages
      .map { it.trim() }
      .filter { it.matches(Regex("[A-Za-z0-9_.]{3,255}")) }
      .take(160)
      .toSortedSet()
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
      .edit()
      .putBoolean(ENABLED, enabled)
      .putStringSet(SELECTED_PACKAGES, packages)
      .apply()
    return config(context)
  }

  fun setExpectedAudioDeviceName(context: Context, value: String?) {
    context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
      .edit()
      .putString(EXPECTED_AUDIO_DEVICE_NAME, value?.trim()?.takeIf { it.isNotEmpty() })
      .apply()
  }

  fun isNotificationAccessGranted(context: Context): Boolean =
    NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)

  fun isPhoneStatePermissionGranted(context: Context): Boolean =
    context.checkSelfPermission(android.Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED

  /**
   * Android has no privacy-preserving "the person is looking at the screen"
   * signal. Interactive plus unlocked is the reliable conservative proxy: do
   * not alert the glasses while the user can already see the phone.
   */
  fun isPhoneActivelyInUse(context: Context): Boolean {
    val powerManager = context.getSystemService(PowerManager::class.java) ?: return false
    val keyguardManager = context.getSystemService(KeyguardManager::class.java) ?: return false
    return powerManager.isInteractive && !keyguardManager.isKeyguardLocked
  }

  fun launchableApps(context: Context): List<Map<String, String>> {
    val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
    return context.packageManager.queryIntentActivities(intent, PackageManager.MATCH_ALL)
      .asSequence()
      .map { it.activityInfo.applicationInfo }
      .filter { it.packageName != context.packageName }
      .distinctBy { it.packageName }
      .map {
        mapOf(
          "packageName" to it.packageName,
          "label" to context.packageManager.getApplicationLabel(it).toString(),
        )
      }
      .sortedWith(compareBy({ it["label"]?.lowercase() }, { it["packageName"] }))
      .toList()
  }

  fun findGlassesAudioDevice(context: Context): AudioDeviceInfo? {
    val expectedName = config(context).expectedAudioDeviceName?.lowercase() ?: return null
    val manager = context.getSystemService(AudioManager::class.java) ?: return null
    return manager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull { device ->
      val name = device.productName?.toString()?.trim()?.lowercase().orEmpty()
      isBluetoothAudioOutput(device) && name.isNotEmpty() &&
        (name.contains(expectedName) || expectedName.contains(name))
    }
  }

  private fun isBluetoothAudioOutput(device: AudioDeviceInfo): Boolean = when (device.type) {
    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
    AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
    AudioDeviceInfo.TYPE_BLE_HEADSET,
    AudioDeviceInfo.TYPE_BLE_SPEAKER,
    -> true
    else -> false
  }

  fun notificationAccessIntent(): Intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}
