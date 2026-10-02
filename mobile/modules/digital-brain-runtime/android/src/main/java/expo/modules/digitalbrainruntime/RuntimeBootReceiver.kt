package expo.modules.digitalbrainruntime

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Boot recovery never mounts React Native or starts Headless JS. */
class RuntimeBootReceiver : BroadcastReceiver() {
  override fun onReceive(context: Context, intent: Intent) {
    if (intent.action !in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED)) return
    if (!RuntimeGlasses.signedIn(context) || !RuntimeGlasses.enabled(context) || !RuntimeGlasses.permitted(context)) return
    try { DigitalBrainRuntime.refresh(context) }
    catch (_: RuntimeException) { DigitalBrainRuntime.lastError = "Glasses boot recovery could not start; open the app" }
  }
}
